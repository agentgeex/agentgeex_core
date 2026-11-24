/**
 * session-history.tsx — "what has been said in this session before" (2026-11-14 11:08 Feng)
 *
 * ★ Why we built it: Feng's exact words
 *   "Now there is a very useful dataset: it holds the detailed content of all conversations, so you can put the detailed
 *     conversation record below the message body in the send window, right? Then the history exists,
 *     and you no longer have to remember things in memory on the fly. This feature is quite useful."
 *
 * ★ Data source: `agx_user_message` (full archive of question + answer; one row = one owner message + its answer)
 *   Read via BFF `GET /v1/sessions/messages?key=<session_key>`.
 *   Join key = `agx_session.session_id` (like `agent:xxx:main`), exactly the session id in the frontend store.
 *
 * ★ Why a separate file instead of stuffing it into the send dialog:
 *   The send dialog is already 2900+ lines and already carries the main send flow; history is a **read-only side path**,
 *   mixing it in would keep inflating that file, and other places (session detail, agent detail) may reuse it later.
 *
 * ★ By default only "human" conversations are shown:
 *   In that table envelope (dispatch envelope) + notice (notification) make up more than half — with everything mixed in
 *   by default, human exchanges drown in the stream. Full volume is an explicit toggle, not "show everything by default".
 *
 * ★ [2026-11-14 12:33 Feng] Three changes, exact words:
 *   "First, put the conversation history above the message body; the history gets longer, the message body can be shorter.
 *     Second, in the history the agent's answer and my question should be offset — the agent answer left-aligned,
 *     my question right-aligned, with a gap in between and different colors.
 *     Second improvement: with the history on top, make it reverse-chronological, like a normal AI interface,
 *     newest at the bottom, older above — that layout."
 *
 *   Landing as three things:
 *   ① Position: moved above ⑦ message body (decided by the dialog); this component supports `fill` to stretch and fill
 *   ② Bubbles: agent answer left / my question right, different background, whitespace between — no more Q&A cards stacked
 *   ③ Order: still chronological (old→new), but **newest at the bottom**, scrolled to the bottom by default;
 *      the "load earlier" button moves from the bottom to the **top** (older is above, so looking up is correct)
 */
import * as React from "react";
import { History, Loader2, RefreshCw, ChevronDown, Info, Maximize2, Mail } from "lucide-react";
import {
  sessionMessagesApi,
  type SessionDispatchInfo,
  type SessionMessageItem,
} from "@/providers/api";
import { MarkdownView } from "@/components/ui/markdown-view";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useTranslation } from "react-i18next";

/** how many records per fetch (one question + answer counts as one) */
/* [2026-11-14 20:40 Feng] "show every message in the database".
   It used to be 8 per page — opening it showed only the last few lines, and the remaining hundreds needed
   repeated "load earlier" clicks; to Feng that is "incomplete display", and he is right: the conversation log
   should be given in full at once. 500 covers the vast majority of sessions on this host (the largest
   agx_ui_lead main session has 496), so one fetch gets everything. */
const PAGE = 500;

const KIND_KEY: Record<SessionMessageItem["kind"], string> = {
  human: "sessionHistory.kind.human",
  envelope: "sessionHistory.kind.envelope",
  notice: "sessionHistory.kind.notice",
  system: "sessionHistory.kind.system",
  command: "sessionHistory.kind.command",
};

/** `2026-11-14 10:57` → `11-14 10:57` (skip the year within the same year to save width) */
function fmtAt(at: string): string {
  if (!at) return "";
  return at.length >= 16 ? at.slice(5, 16) : at;
}

/**
 * "Preview cleanup" of Markdown markup: in the collapsed state only the first 4 lines are shown, with symbols
 * like ## / ** / ` stripped — otherwise opening the list shows "## what changed" and "**key points**", which looks coarse.
 *
 * ★ Cleanup only in the **collapsed** state; the expanded state goes to MarkdownView (two things, not mixed).
 *   No full parsing: collapsed only needs to "read like plain language"; parsing it again is wasted work.
 */
function stripMd(s: string): string {
  return s
    .replace(/```[\w-]*\n?/g, "")           // code fence
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")     // heading
    .replace(/^\s{0,3}>\s?/gm, "")         // quote
    .replace(/\*\*([^*]+)\*\*/g, "$1")      // bold
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1$2") // italic
    .replace(/`([^`]+)`/g, "$1")            // inline code
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1"); // link → text
}

/**
 * A body that may be very long: truncate beyond 4 lines, click to expand.
 *
 * ★ Not expanded by default: real data has messages of thousands of characters (a whole voice transcript);
 *   expanded, one screen shows only a single message and the history list loses its "glance" value.
 * ★ [2026-11-14 11:15 Feng "I want this"] answers (markdown=true) render as **real Markdown** when expanded
 *   (headings/lists/tables/code blocks all styled), and as cleaned plain text when collapsed.
 *   Why not render markdown when collapsed too: parsing 8 × thousands of characters at once noticeably slows the dialog,
 *   while the collapsed state only shows 4 lines — rendering makes no difference to reading, so it is not worth it.
 */
function CollapsibleText({
  text,
  tone,
  markdown = false,
  testId,
}: {
  text: string;
  tone: "q" | "a";
  /** render as Markdown when expanded (agent answers only; a human voice transcript must not be treated as markdown) */
  markdown?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = React.useState(false);
  if (!text) {
    /* [2026-11-14 20:45 Feng] "After I send a message, the system returns
       (no text answer this turn · pure tool call or unanswered), what is this? What does this hint mean?"
       ★ The original copy had two flaws:
         ① "pure tool call" is internal jargon users cannot understand;
         ② it merged **three different situations** into one sentence —
            a. this turn really had only tool calls (the agent said nothing)
            b. the answer was merged into the next message (when two are sent in a row, one answer pairs with one)
            c. the collector has not collected it yet / never collected it
          Measured on 2026-11-14 20:31, that case was b: `reply_content` empty in the DB and
          `reply_at` null, but the agent did reply — the content was recorded under the 20:36 entry.
          Calling it "no text answer" is a **wrong judgement**, not merely poor wording.
       → Changed to state only the fact "no answer collected for this turn", with possible causes explained in the title,
         no longer drawing the "it did not answer" conclusion for the user. */
    return (
      <p
        className="text-xs italic opacity-70"
        data-testid={testId}
        title={t("sessionHistory.noReplyHint")}
      >
        {t("sessionHistory.noReply")}
      </p>
    );
  }
  /* Rough threshold: about 40 characters per line (12px CJK chars + container width), 4 lines ≈ 160 characters.
     An "expand" button on short content is meaningless, so below the threshold the whole text is shown. */
  const long = text.length > 160 || text.split("\n").length > 4;

  /* short content + markdown: render directly, no expand (it is not long anyway) */
  if (markdown && !long) {
    return (
      <div className="text-xs" data-testid={testId}>
        <MarkdownView source={text} compact bare className="[&_.wmde-markdown]:text-xs" />
      </div>
    );
  }

  /* [2026-11-14 21:00] In the collapsed state only **the visible lines** go into the DOM.
     Measured "show all 497 at once": 12.5s to render; the bulk was 355 answers each pushing thousands of
     characters into text nodes and then clipping with `line-clamp-4` — clipping the display, not the DOM.
     4 lines ≈ 160 characters; take 240 to leave margin (whitespace/newline differences), the rest is invisible anyway.
     ★ Visually equivalent: collapsed content looks exactly as before (trailing `…` indicates there is more). */
  const CLIP = 240;
  const clipped = !open && text.length > CLIP;
  const shown = open ? text : text.slice(0, CLIP);

  return (
    <div data-testid={testId}>
      {open && markdown ? (
        <MarkdownView source={text} compact bare className="[&_.wmde-markdown]:text-xs" />
      ) : (
        <p
          className={cn(
            "whitespace-pre-wrap break-words text-xs leading-relaxed",
            long && !open && "line-clamp-4",
          )}
        >
          {markdown ? stripMd(shown) : shown}
          {clipped ? "…" : ""}
        </p>
      )}
      {long && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="mt-0.5 inline-flex items-center gap-0.5 text-[11px] opacity-60 transition-opacity hover:opacity-100"
          data-testid={testId ? `${testId}-toggle` : undefined}
        >
          <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
          {open ? t("sessionHistory.collapse") : t("sessionHistory.expand", { n: text.length })}
        </button>
      )}
    </div>
  );
}

/**
 * Locate the "this instruction" section inside `exec_instruction` (for the **list collapsed state**; the dialog shows all).
 *
 * [Added only after measuring on 2026-11-14 20:20] The first version collapsed `exec_instruction` as-is over 3 lines,
 * so the first screen was all `<!-- agx:session-ctx -->` session context (metadata meant for the agent),
 * and the real instruction sat 1000 characters later — Feng's words were "I cannot see the instruction message",
 * so it was effectively still unseen.
 *
 * ★ This is not "trimming" (no information lost): the list only **puts the excerpt on the informative section**;
 *   opening the dialog still shows the full `exec_instruction`, not one character less.
 * ★ The markers (the "this instruction" / "task description" tags) are a **fixed structure** generated by the kernel
 *   when assembling the text, not guessed from semantics; if no marker is found the whole text is shown, no guessing.
 */
/**
 * [2026-11-14 20:28 discovered by measurement] MarkdownView eats `<!-- agx:session-ctx -->`
 * as an HTML comment — measured: a 3,303-character body rendered down to 2,909 characters.
 * What Feng wants is "know what is really inside the message"; **losing a paragraph betrays that requirement**.
 * → Before rendering, turn HTML comments into inline code so they stay visible as-is.
 */
function keepHtmlComments(text: string): string {
  return text.replace(/<!--([\s\S]*?)-->/g, (_m, inner) => "`<!--" + inner + "-->`");
}

function pickCoreInstruction(text: string): { core: string; focused: boolean } {
  const M = "【本次指令】";
  const END = "【任务说明书】";
  const i = text.indexOf(M);
  if (i < 0) return { core: text, focused: false };
  const j = text.indexOf(END, i + M.length);
  const core = (j > i ? text.slice(i + M.length, j) : text.slice(i + M.length)).trim();
  if (!core) return { core: text, focused: false };
  return { core, focused: true };
}

/** msg_type → plain language (pure function; t injected by the caller) */
function dirLabel(msgType: string, t: (k: string) => string): string {
  if (msgType === "receipt") return t("sessionHistory.dir.receipt");
  if (msgType === "dispatch") return t("sessionHistory.dir.dispatch");
  if (msgType === "urge") return t("sessionHistory.dir.urge");
  return t("sessionHistory.dir.other");
}

/**
 * One "dispatch / receipt" envelope (`msg_kind='envelope'`).
 *
 * [2026-11-14 19:55 Feng] "The instruction message is still invisible in the UI".
 * Data side clarified: the envelope only holds `{"task_run_uuid":..., "send_agent":..., "msg_type":...}`;
 * **the instruction body is not in the message table at all** — after receiving the envelope the agent itself uses
 * run_uuid to fetch `exec_instruction` from `agx_task_execution`. So this row could originally show only a raw JSON
 * blob, i.e. nothing at all.
 *
 * [2026-11-14 20:03 Feng] "This type of message also has to be displayed... click it to pop a window,
 *   and the window shows the details of this message..." — show an excerpt in the list + whole block clickable.
 *
 * ★ Why not expand the full text inline in the list: Feng wants "click to see details", and the body is often
 *   2000–4000 characters; inline expansion would blow up the whole history panel. The dialog has its own scroll
 *   area, and closing it does not move the list.
 */
/** [2026-11-15 19:52 Feng] ① instruction messages **right-aligned** (changing them to left earlier was my misreading)
 *   "Instructions and text messages are actually both sent by me, just sent by the system on my behalf, so instructions
 *     should still be right-aligned like text messages; the info block above (meta line) too."
 *  ② the envelope **lives inside the message content, no extra entry point outside**:
 *   "I want this envelope displayed inside the content, not add something outside... I just want, on the message list
 *     without clicking, put the envelope at the very top, so I see at a glance it is an envelope."
 *   —— so inside the card, top to bottom = envelope (raw JSON, not collapsed) → instruction body → click for full text.
 */
function EnvelopeRow({ m, onOpen }: { m: SessionMessageItem; onOpen: () => void }) {
  const { t } = useTranslation();
  const d: SessionDispatchInfo | null = m.dispatch;
  const dir = d?.msgType === "receipt" ? "receipt" : "dispatch";
  const who = d?.agentId || d?.sendAgent || "";
  const title = d?.execName || (d?.runUuid ? d.runUuid.slice(0, 18) : "");
  const body = d?.instruction || "";
  const core = body ? pickCoreInstruction(body).core : "";
  /* real total length (the list carries only a 600-character preview; `instructionLen` is the full length) */
  const fullLen = d?.instructionLen || body.length;
  const short = d?.runUuid ? d.runUuid.replace(/^run_general_/, "").slice(0, 8) : "";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onOpen()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
      className="group flex cursor-pointer flex-col items-end gap-1 outline-none"
      title={t("sessionHistory.envelope.openTitle")}
      data-testid={`uib-env-${m.id}`}
    >
      {/* meta line: right-aligned with the bubble (Feng: "including the info block above — right-aligned too") */}
      <div className="flex max-w-[94%] flex-wrap items-center justify-end gap-1.5 text-[10px] text-muted-foreground">
        <span
          className={cn(
            "rounded px-1 py-px font-medium",
            dir === "receipt" ? "bg-success/15 text-success" : "bg-primary/15 text-primary",
          )}
          data-testid={`uib-env-dir-${m.id}`}
        >
          {dir === "receipt" ? t("sessionHistory.dir.receiptArrow") : t("sessionHistory.dir.dispatchArrow")}
        </span>
        {who && <span className="font-mono">{who}</span>}
        {title && <span className="truncate text-foreground">{title}</span>}
        {short && <span className="font-mono opacity-60">{short}</span>}
        <span className="font-mono">{fmtAt(m.at)}</span>
      </div>
      {/* [2026-11-15 19:52 Feng] the envelope goes **inside the content, at the very top**, not collapsed:
          "first the envelope, then below it the letter" —— while debugging you see at a glance that this is an
          envelope and what is inside it, and further down is the instruction it carries. The old "add an envelope
          button outside" approach has been removed. */}
      <div className="w-full max-w-[94%] rounded-md border border-dashed bg-primary/10 px-2.5 py-1.5 transition-colors group-hover:border-primary/40 group-hover:bg-primary/15">
        <div className="mb-1 flex items-center gap-1 text-[10px] text-muted-foreground">
          <Mail className="h-2.5 w-2.5" />
          {t("sessionHistory.envelope.title")}
          {d?.msgType && <span className="font-mono opacity-70">{d.msgType}</span>}
        </div>
        <pre
          className="max-h-24 overflow-y-auto whitespace-pre-wrap break-all rounded border bg-card/70 px-1.5 py-1 font-mono text-[10px] leading-relaxed text-foreground/80"
          data-testid={`uib-env-raw-${m.id}`}
        >
          {m.content}
        </pre>
        <div className="mt-1 text-[10px] text-muted-foreground">{t("sessionHistory.envelope.instruction")}</div>
        {core ? (
          <p className="line-clamp-4 whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground">
            {core}
          </p>
        ) : (
          <p className="text-[11px] italic text-muted-foreground">{t("sessionHistory.envelope.noInstruction")}</p>
        )}
        <span className="mt-1 inline-flex items-center gap-0.5 text-[10px] text-muted-foreground transition-colors group-hover:text-primary">
          <Maximize2 className="h-2.5 w-2.5" />
          {body ? t("sessionHistory.envelope.viewDetailWithLen", { n: fullLen }) : t("sessionHistory.envelope.viewDetail")}
        </span>
      </div>
    </div>
  );
}

/**
 * [2026-11-17 08:50 PM reported + Feng reported] When OpenClaw is in "your previous turn still holds it, queue this one",
 * it prepends this English wrapper before the human's own words, and the collector follows the rule that
 * **raw content is always verbatim** (no trimming), so 278 human messages all carry this English line at the head:
 *   `[Queued user message from a previous active turn; preserved as context only.
 *     Continue with the active prompt below.]`
 *
 * ★ Why strip it here and not change the backend:
 *   ① the original text in the DB is **evidence** (who was wrapped how, and when); changing it kills retrospection;
 *   ② this is a **display** problem — display rules belong to the UI, data fidelity to the collector. One each.
 * ★ Strip only the **exact prefix**, no fuzzy matching: loosen the regex and a human typing brackets gets eaten too.
 * ★ After stripping the user must still know "this one was processed late" (on the timeline it sits minutes away
 *   from its neighbours; unmarked, people think the agent is slow), so return `queued` and let the bubble carry a mark.
 */
const QUEUED_PREFIX =
  "[Queued user message from a previous active turn; preserved as context only. Continue with the active prompt below.]";

function splitQueued(raw: string): { text: string; queued: boolean } {
  const s = (raw || "").trimStart();
  if (!s.startsWith(QUEUED_PREFIX)) return { text: raw || "", queued: false };
  return { text: s.slice(QUEUED_PREFIX.length).replace(/^[\s\n]+/, ""), queued: true };
}

/**
 * The "agent answer" block — shared as **one single copy** by three places (human / envelope / system etc.).
 *
 * [2026-11-16 14:09 PM relayed from Feng] "After sending an instruction I only see the instruction, not the reply".
 * ★ Root cause is neither data nor refresh: **a render branch was missing**.
 *   The `human` branch drew the reply; the `envelope` branch (EnvelopeRow) and the centered non-human branch drew
 *   only `content` — while in the DB the answers on envelope rows **really exist**:
 *   measured over the last 7 days in `agx_user_message`: of 372 envelope rows, 342 have `reply_content`.
 *   In other words "dispatched instructions do have answers, but the UI never shows them".
 * ★ Why extract a component instead of writing it three times: one missed update recreates the odd difference
 *   "this kind of message has an answer, that kind does not" — display rules must live in exactly one place.
 */
function ReplyBlock({ m }: { m: SessionMessageItem }) {
  const { t } = useTranslation();
  if (!m.reply || !m.reply.trim()) return null;
  return (
    <div className="flex flex-col items-start gap-0.5">
      <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
        <span className="rounded bg-muted px-1">{t("sessionHistory.reply.answer")}</span>
        <span className="font-mono">{fmtAt(m.replyAt || m.at)}</span>
        {m.replyParts > 1 && (
          <span title={t("sessionHistory.reply.toolCallsHint")}>{t("sessionHistory.reply.toolCalls", { n: m.replyParts })}</span>
        )}
      </div>
      <div className="max-w-[88%] rounded-lg rounded-tl-sm border border-border/70 bg-card px-3 py-2 shadow-sm">
        <CollapsibleText text={m.reply} tone="a" markdown testId={`uib-history-a-${m.id}`} />
      </div>
    </div>
  );
}

/**
 * Envelope detail dialog: envelope (pinned on top, not collapsed) → full instruction → metadata.
 *
 * [2026-11-15 19:52 Feng] "In the dialog the envelope still sits on top, first the envelope, then below it the letter"
 * —— the envelope is the debugging entry and the "shell" of the message, so it precedes the instruction.
 *
 * [2026-11-14 20:03 Feng] "After popping a window it can show the details of this message, so I know what is
 *   inside it... in future operation most of them are that kind of message, because once it enters the DB
 *   it is archived."
 * → So the "raw envelope" is also kept visible (that is the JSON string Feng pasted),
 *   making "what is actually stored in the message" checkable, not our paraphrased version.
 */
function EnvelopeDetailDialog({
  m,
  onClose,
}: {
  m: SessionMessageItem | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const d = m?.dispatch || null;
  /* [2026-11-14 20:52] the list carries only a 600-character preview → fetch full text by run_uuid when the dialog opens.
     If unavailable (execution cleaned up / no run_uuid), fall back to the preview and say so. */
  const [full, setFull] = React.useState<string | null>(null);
  const [fetching, setFetching] = React.useState(false);
  const [fetchErr, setFetchErr] = React.useState("");
  const runUuid = d?.runUuid || "";
  React.useEffect(() => {
    setFull(null);
    setFetchErr("");
    if (!m || !runUuid || !d?.instructionTruncated) return;
    let alive = true;
    setFetching(true);
    sessionMessagesApi
      .instruction(runUuid)
      .then((r) => alive && setFull(r.instruction || ""))
      .catch((e) => alive && setFetchErr(String((e as Error)?.message || e)))
      .finally(() => alive && setFetching(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m?.id, runUuid]);

  const preview = d?.instruction || "";
  const body = full ?? preview;
  const bodyLen = d?.instructionLen || body.length;
  const isPartial = !full && !!d?.instructionTruncated;

  const rows: Array<[string, string]> = m
    ? [
        [
          t("sessionHistory.envelope.meta.msgType"),
          `${dirLabel(d?.msgType || "", t)}${d?.msgType ? ` · ${d.msgType}` : t("sessionHistory.envelope.meta.msgTypeNoHeader")}`,
        ],
        [t("sessionHistory.envelope.meta.runUuid"), d?.runUuid || t("sessionHistory.envelope.meta.runUuidMissing")],
        [t("sessionHistory.envelope.meta.execName"), d?.execName || t("sessionHistory.envelope.meta.execNameMissing")],
        [t("sessionHistory.envelope.meta.sender"), d?.sendAgent || d?.sendAgentId || "—"],
        [t("sessionHistory.envelope.meta.runner"), d?.agentId || "—"],
        [t("sessionHistory.envelope.meta.runStatus"), d?.runStatus || "—"],
        [t("sessionHistory.envelope.meta.at"), m.at],
        [
          t("sessionHistory.envelope.meta.bodyLen"),
          body
            ? t("sessionHistory.envelope.bodyLen", {
                n: bodyLen,
                suffix: isPartial
                  ? fetching
                    ? ` · ${t("sessionHistory.envelope.fetchingFull")}`
                    : ` · ${t("sessionHistory.envelope.previewOnly")}`
                  : ` · ${t("sessionHistory.envelope.fullText")}`,
              })
            : t("sessionHistory.envelope.noBody"),
        ],
      ]
    : [];
  /* the monospace items (matched by the translated **labels**, so the same set of keys must be used) */
  const keyword = [
    t("sessionHistory.envelope.meta.runUuid"),
    t("sessionHistory.envelope.meta.execName"),
    t("sessionHistory.envelope.meta.sender"),
    t("sessionHistory.envelope.meta.runner"),
    t("sessionHistory.envelope.meta.runStatus"),
    t("sessionHistory.envelope.meta.at"),
    t("sessionHistory.envelope.meta.bodyLen"),
  ];

  return (
    <Dialog open={!!m} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-4xl gap-0 p-0" data-testid="uib-env-dialog">
        <DialogHeader className="shrink-0 border-b px-5 py-3 pr-12">
          <DialogTitle className="text-base" data-testid="uib-env-dialog-title">
            {dirLabel(d?.msgType || "", t)}
            {d?.execName ? ` · ${d.execName}` : ""}
            {d?.runUuid ? <span className="ml-1.5 font-mono text-xs font-normal text-muted-foreground">{d.runUuid}</span> : null}
          </DialogTitle>
          <DialogDescription className="text-xs">
            {t("sessionHistory.envelope.dialogDesc")}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[80vh] overflow-y-auto px-5 py-4">
          {/* [2026-11-15 19:52 Feng] the envelope is **pinned on top, not collapsed**:
              "In the dialog the envelope still sits on top, first the envelope, then below it the letter"
              —— the envelope outweighs the instruction: check the envelope is right first, then the instruction it carries.
              What was here before was a "view raw envelope" collapse toggle, collapsed by default; the block is gone. */}
          {m && (
            <div className="mb-4">
              <div className="mb-1.5 flex items-center gap-1 text-xs font-medium">
                <Mail className="h-3 w-3" />
                {t("sessionHistory.envelope.title")}
                <span className="font-normal text-muted-foreground">{t("sessionHistory.envelope.processed")}</span>
              </div>
              <pre
                className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/40 p-2.5 font-mono text-[11px] leading-relaxed"
                data-testid="uib-env-raw"
              >
                {m.content}
              </pre>
            </div>
          )}

          {/* instruction body */}
          <div className="mt-4">
            <div className="mb-1.5 flex items-center gap-2 text-xs font-medium">
              {t("sessionHistory.envelope.bodyTitle")}
              {body && (
                <span className="font-normal text-muted-foreground">
                  {t("sessionHistory.envelope.bodyLen", {
                    n: bodyLen,
                    suffix: isPartial
                      ? ` · ${t("sessionHistory.envelope.previewOnly")}`
                      : ` · ${t("sessionHistory.envelope.fullText")}`,
                  })}
                </span>
              )}
              {fetching && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
              {fetchErr && (
                <span className="font-normal text-destructive">
                  {t("sessionHistory.envelope.fetchFailed", { msg: fetchErr })}
                </span>
              )}
            </div>
            {body ? (
              <div
                className="max-h-[46vh] overflow-y-auto rounded-md border bg-card p-3"
                data-testid="uib-env-dialog-body"
              >
                <MarkdownView
                  source={keepHtmlComments(body)}
                  compact
                  bare
                  className="[&_.wmde-markdown]:text-xs"
                />
              </div>
            ) : (
              <p className="rounded-md border border-dashed p-3 text-xs italic text-muted-foreground">
                {t("sessionHistory.envelope.noBodyHint")}
              </p>
            )}
          </div>

          {/* metadata: the envelope is already pinned on top, so details go last as reference */}
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1.5 text-xs">
            {rows.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="whitespace-nowrap text-muted-foreground">{k}</dt>
                <dd
                  className={cn(
                    "min-w-0 break-words",
                    keyword.includes(k) ? "font-mono text-foreground" : "text-foreground",
                  )}
                  data-testid={`uib-env-meta-${k}`}
                >
                  {v}
                </dd>
              </React.Fragment>
            ))}
          </dl>

        </div>
      </DialogContent>
    </Dialog>
  );
}

/** local message pending persistence (shown right after a successful send, replaced by the real row once collected) */
interface PendingSent {
  key: string;
  /**
   * [2026-11-14 22:52 Feng] Which **session** this placeholder message belongs to.
   *
   * Exact words: "You seem to have forgotten, go by session, because each session is different, you must bind to
   *   the session. I just tested it: I sent messages in different sessions, and every session ended up with
   *   unprocessed messages."
   *
   * Root cause: `pending` is the component's own state, and the component **does not remount when sessionKey changes**;
   * the old render/polling ignored the session → a message sent to A showed up in B's history,
   * and matching A's text against B's latest 30 always failed → a false "not found in DB" report after 60 seconds.
   */
  sessionKey: string;
  /**
   * The body text used for display.
   * [2026-11-14 21:12 Feng] "If the message body content is, say, task, then the message body content should be
   *   what is inside the packaged task. But in the conversation history above you still put the content of the
   *   input box below the message body... what you put in there seems different from what you sent out"
   * → Task/quick command mode: `text` is replaced by the `exec_instruction` the kernel actually dispatches
   *   (session context + this instruction + task description). **Never pass off input-box content as what was sent.**
   */
  text: string;
  /** raw text used for DB matching (meaningful only in text mode; task mode matches the envelope by runUuid) */
  matchText: string;
  at: number;
  /** text=text / task=task / quickcmd=quick command */
  mode: string;
  /** target agent / task name, for label display only */
  note?: string;
  /** task / quick command mode: execution id — gives both the real instruction and the DB-matching key */
  runUuid?: string;
  /** loading the instruction the kernel actually dispatched */
  loadingReal?: boolean;
  /** character count of the real instruction (0 = not fetched yet) */
  realLen?: number;
  /** waiting=3s fast poll / slow=1min later reduced-frequency poll / timeout=give up after 10min
   *  [2026-11-14 22:58 Feng] "After this message was already in the DB, your check broke again"
   *  → it used to be "stop if not found within 60 seconds", so a slightly slow collector stayed
   *    at "not found" forever, even though the message was already stored. Changed to
   *    "keep polling, only reduce frequency": as long as 10 minutes have not passed,
   *    a row arriving later can still replace the placeholder with the real row. */
  status: "waiting" | "slow" | "timeout";
}

/** actions the parent (send dialog) drives through ref */
export interface SessionHistoryHandle {
  /**
   * [2026-11-14 20:31 Feng] After a successful send, **insert this message into the history list above**,
   * instead of opening another "N sent" box below.
   *
   * Exact words: "When you click send, do not open another box below, it is unnecessary now,
   *   and it takes space; just add the message you sent into the conversation history above.
   *   At this point there is refresh and auto refresh, right... after the backend stores it automatically,
   *   you refresh it out."
   */
  appendSent: (text: string, meta?: { mode?: string; note?: string; runUuid?: string; sessionKey?: string }) => void;
}

export interface SessionHistoryProps {
  /** session addressing key (= agx_session.session_id). Empty = no target session selected yet. */
  sessionKey: string;
  /** session display name (for empty-state copy only, not used for fetching) */
  sessionName?: string;
  className?: string;
  /** whether to show the "all types" toggle (shown by default) */
  showKindToggle?: boolean;
  /**
   * [2026-11-14 12:33 Feng] When true the list height is decided by the parent container (`flex-1`),
   * no longer fixed `max-h-[240px]` — moved above the body it must eat the remaining space of the right column.
   */
  fill?: boolean;
}

export const SessionHistory = React.forwardRef<SessionHistoryHandle, SessionHistoryProps>(
  function SessionHistory(
    { sessionKey, sessionName = "", className, showKindToggle = true, fill = false },
    ref,
  ) {
  const { t } = useTranslation();
  const [items, setItems] = React.useState<SessionMessageItem[]>([]);
  const [total, setTotal] = React.useState(0);
  const [hasMore, setHasMore] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [loadingMore, setLoadingMore] = React.useState(false);
  const [error, setError] = React.useState("");
  /* manual refresh signal: messages keep landing in the DB, the user may just want to "see the line just sent" */
  const [tick, setTick] = React.useState(0);
  /* time of the last completed auto refresh (shown in the title row so it is clear the panel is alive) */
  const [autoAt, setAutoAt] = React.useState("");

  const listRef = React.useRef<HTMLDivElement | null>(null);
  /* latest value of items (for scheduled refresh: merge decision outside the setState updater;
     the updater runs deferred, so assigning `changed` inside it reads a stale value — the first version died here) */
  const itemsRef = React.useRef<SessionMessageItem[]>([]);
  React.useEffect(() => {
    itemsRef.current = items;
  }, [items]);
  /* [2026-11-14 20:03 Feng] click an envelope row to see details */
  const [detail, setDetail] = React.useState<SessionMessageItem | null>(null);

  /* ── local message pending persistence (2026-11-14 20:31 Feng)────────────────
     After a successful send it shows immediately at the end of the list (grey dashed box + "pending"),
     then the DB is polled every 3 seconds; on a hit the real row replaces it and the local placeholder goes away.
     ★ Why not "pretend it is stored" right after sending: the DB is the only truth.
       Showing it locally is for "I clicked send, the screen reacts immediately"; whether it is really stored
       is decided by the DB.
     ★ Why not WebSocket/SSE: the collector is a separate process writing in batches
       (see tools/user_msg_collect.py), there is no "stored" event to subscribe to — you can only ask. */
  const [pending, setPending] = React.useState<PendingSent[]>([]);
  /* show only pending rows belonging to the current session — rows of other sessions stay untouched and
   are still there when switching back. */
  const mine = React.useMemo(
    () => (sessionKey ? pending.filter((p) => p.sessionKey === sessionKey) : []),
    [pending, sessionKey],
  );
  /** full message-type set — main query / pending poll / incremental refresh **must all use the same one**.
   *  ★ The route layer defaults to the full set when kinds is omitted, but `realdata.session_messages`
   *    defaults to `("human",)`. Mismatched defaults = the day the call chain changes a layer, only human
   *    text is silently read and envelope (task envelope) counts as "not stored". All three frontend call
   *    sites pass it explicitly. */
  const kinds = React.useMemo(
    () => ["human", "envelope", "notice", "system", "command"],
    [],
  );
  /** latest value of the current session (the `useImperativeHandle` deps are empty, so the closure sees no new props) */
  const sessionKeyRef = React.useRef(sessionKey);
  sessionKeyRef.current = sessionKey;

  /* [2026-11-14 20:40 Feng] "Do not split system messages from non-system messages;
     anything in conversation form must be sent here, forget whether it is system or not"
     → no more two-level filtering, **always full volume**: human / envelope / notice / system / command
     all shown, ordered by time. Classification is now done by "how it looks" (bubble / centered / envelope
     row), not by "hiding it". */
  /* [2026-11-14 20:44 found by measurement] The pending row was added but the list **did not scroll to the
     bottom** — in the screenshot the just-sent line was invisible, you had to scroll down yourself.
     Feng wants "after sending, I see it above"; invisible = not done. → scroll to bottom as soon as pending arrives. */
  React.useEffect(() => {
    if (!mine.length) return;
    requestAnimationFrame(() => {
      const el = listRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mine.length]);

  React.useImperativeHandle(
    ref,
    () => ({
      appendSent: (text: string, meta) => {
        const t = (text || "").trim();
        const runUuid = meta?.runUuid || "";
        if (!t && !runUuid) return;
        const key = `p_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
        setPending((prev) => [
          ...prev,
          {
            key,
            /* ★ The owning session must be fixed **at the moment of sending**: when a task is sent to a new session,
              props.sessionKey is still empty, the real landing point comes from out.session_key. */
            sessionKey: meta?.sessionKey || sessionKeyRef.current || "",
            text: t,
            matchText: t,
            at: Date.now(),
            mode: meta?.mode || "text",
            note: meta?.note,
            runUuid,
            /* task / quick command: immediately fetch "the instruction that was really sent".
               Text mode has no run_uuid (agx_send_text creates no execution), so it can only show the body itself. */
            loadingReal: !!runUuid,
            status: "waiting",
          },
        ]);
        if (!runUuid) return;
        sessionMessagesApi
          .instruction(runUuid)
          .then((r) => {
            const real = r.instruction || "";
            setPending((prev) =>
              prev.map((x) =>
                x.key === key
                  ? { ...x, text: real || x.text, realLen: real.length, loadingReal: false }
                  : x,
              ),
            );
          })
          .catch(() =>
            setPending((prev) =>
              prev.map((x) => (x.key === key ? { ...x, loadingReal: false } : x)),
            ),
          );
      },
    }),
    [],
  );

  /* polling: wait for the collector to write this row into agx_user_message, then swap in the real row.
     ★ Cadence (changed by [2026-11-14 22:58 Feng]):
       first minute every 3 seconds (fast, normal collection is on that order)
       after 1 minute drop to every 15 seconds, keep polling until 10 minutes
       —— never "stop if not found within 60 seconds": measured, the collector writes in batches and can be
          far slower than 60s; once stopped, messages that really do land later stay marked "not found" forever. */
  React.useEffect(() => {
    const active = pending.filter(
      (x) => (x.status === "waiting" || x.status === "slow") && x.sessionKey === sessionKey,
    );
    if (!sessionKey || active.length === 0) return;
    const delay = active.some((x) => x.status === "waiting") ? 3000 : 15000;
    let alive = true;
    const timer = setTimeout(async () => {
      if (!alive) return;
      try {
        /* query only the latest 30 — looking for "the line I just sent", no need for the whole table
           ★ Pass kinds explicitly: the route layer returns the full set when omitted, but the realdata layer
           defaults to human; never rely on "the upper layer happens to default correctly" for envelope. */
        const r = await sessionMessagesApi.list(sessionKey, { limit: 30, kinds });
        if (!alive) return;
        const rows = r.items || [];
        const matched: SessionMessageItem[] = [];
        const still: PendingSent[] = [];
        for (const p of pending) {
          /* ★ Handle only the current session: other sessions' rows stay as-is (visible and polled after switching back). */
          if (p.sessionKey !== sessionKey) {
            still.push(p);
            continue;
          }
          if (p.status !== "waiting" && p.status !== "slow") {
            still.push(p);
            continue;
          }
          /* [2026-11-14 21:12] task / quick command stores an `envelope` (content is a JSON envelope),
             not human text — matching by content never succeeds, so after 60 seconds it always falsely
             reports "not stored". → when runUuid exists match on the envelope's `task_run_uuid`; only
             text mode matches by body.
             [2026-11-14 22:58] text mode used **exact equality** — once anything is appended to the body
             (e.g. with reference skills selected, BFF appends the reference-skills block), it never matches.
             Changed to "exact or contains" plus a **time lower bound** against mismatches (rows older than
             2 minutes before the send do not count). */
          const hit = p.runUuid
            ? rows.find((it) => (it.dispatch?.runUuid || "") === p.runUuid)
            : rows.find((it) => {
                if (it.kind !== "human") return false;
                const c = (it.content || "").trim();
                if (!c || !p.matchText) return false;
                const t = Date.parse(it.atIso || it.at);
                if (Number.isFinite(t) && t < p.at - 120_000) return false;
                return c === p.matchText || c.includes(p.matchText);
              });
          if (hit) matched.push(hit);
          else still.push(p);
        }
        if (matched.length) {
          /* append after de-duplicating by the real id from the DB; ordering relies on the existing "chronological" invariant.
             ★ Increase the total only when something was **really appended**: the 15s incremental refresh may
               already have pulled this row in; in that case items already contains it and adding to total gives
               "N+1 total but only N rows". */
          const seen = new Set(itemsRef.current.map((x) => x.id));
          const add = matched.filter((x) => !seen.has(x.id));
          if (add.length) {
            setItems((prev) => [...prev, ...add]);
            setTotal((t) => t + add.length);
          }
        }
        /* state progression: within 1 minute = fast poll; 1~10 minutes = reduced frequency; give up after 10 minutes. */
        const now = Date.now();
        setPending(
          still.map((p) => {
            if (p.sessionKey !== sessionKey) return p;
            if (p.status !== "waiting" && p.status !== "slow") return p;
            const age = now - p.at;
            if (age > 600_000) return { ...p, status: "timeout" as const };
            if (age > 60_000) return { ...p, status: "slow" as const };
            return p;
          }),
        );
        /* new row stored → scroll to the bottom so the user sees the line just sent really arrived */
        if (matched.length) {
          requestAnimationFrame(() => {
            const el = listRef.current;
            if (el) el.scrollTop = el.scrollHeight;
          });
        }
      } catch {
        /* a failed query is not a failed write: ask again next round */
      }
    }, 3000);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey, pending]);

  /* ── scheduled incremental refresh (2026-11-14 20:50 Feng asked "is there any timed refresh")──────
     ★ At that time there was **none** — only "click ⟳ manually" and "wait for your own row to land".
       A session is alive (the agent is running, others are sending too); without clicking it stays frozen
       at the moment it was opened.
     ★ Why incremental instead of re-pulling the whole table: 439 rows ≈ 1.3MB, every 15 seconds is pure waste.
       Here only the latest 20 are pulled and merged by id — about 30KB, cheap enough to run continuously.
     ★ Why "overwrite even on the same id": a record first has only the question (reply empty); after the
       agent answers, `reply_content` is filled in. Without overwriting, that answer would never appear.
     ★ Three non-intrusions:
       ① no refresh when the page is hidden (switched to another tab) — do not burn resources when nobody looks
       ② no scrolling when the user is reading older messages above (not at the bottom) — do not yank them away
          with "new messages"
       ③ no loading state — swap data silently in the background, no flicker
  */
  React.useEffect(() => {
    if (!sessionKey) return;
    let alive = true;
    /* [2026-11-16 14:09] Extract "pull once and merge" into a function: the timer and "tab became visible"
        share one copy, so two hand-written versions cannot drift apart later. */
    const pull = async () => {
      if (!alive) return;
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        /* ★ kinds is needed here too: querying human only would never let later envelopes (task receipts etc.)
           come in through incremental refresh — present in the main list, but unchanged after a refresh. */
        const r = await sessionMessagesApi.list(sessionKey, { limit: 20, kinds });
        if (!alive) return;
        const rows = r.items || [];
        if (!rows.length) return;
        const byId = new Map(rows.map((x) => [x.id, x]));
        const el = listRef.current;
        /* whether the list was pinned to the bottom before refreshing — decides whether to follow afterwards */
        const atBottom = !el || el.scrollHeight - el.scrollTop - el.clientHeight < 24;

        /* ★ The merge decision lives **outside** setState: a functional updater runs asynchronously;
           assigning `changed` inside it and reading it outside gives the initial false —
           that is why the first version "updated the data but never showed the update time in the title row". */
        const prev = itemsRef.current;
        let changed = false;
        const merged = prev.map((x) => {
          const n = byId.get(x.id);
          if (!n) return x;
          /* only swap the object when content really changed, avoiding pointless re-renders */
          if (n.reply !== x.reply || n.content !== x.content || n.at !== x.at) {
            changed = true;
            return n;
          }
          return x;
        });
        const seen = new Set(prev.map((x) => x.id));
        const add = rows.filter((x) => !seen.has(x.id));
        if (add.length) changed = true;
        if (changed) setItems(add.length ? [...merged, ...add] : merged);
        setTotal(r.total || 0);
        if (changed) {
          setAutoAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
          if (atBottom) {
            requestAnimationFrame(() => {
              const box = listRef.current;
              if (box) box.scrollTop = box.scrollHeight;
            });
          }
        }
      } catch {
        /* a single failure stays silent: continue next round */
      }
    };
    const iv = setInterval(() => void pull(), 15000);
    /* [2026-11-16 14:09] While the page is hidden the check above short-circuits on `document.hidden` (no cost when
        nobody looks) — but the moment the user **switches back** is exactly when they want new content, and
        waiting for the next 15 seconds is not acceptable. Measured scenario: after sending an instruction the
        user went off for two and a half minutes, the agent finished, and switching back still showed the old view. */
    const onVisible = () => {
      if (typeof document !== "undefined" && !document.hidden) void pull();
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }
    return () => {
      alive = false;
      clearInterval(iv);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionKey]);

  React.useEffect(() => {
    if (!sessionKey) {
      setItems([]);
      setTotal(0);
      setHasMore(false);
      setError("");
      return;
    }
    let alive = true;
    setLoading(true);
    setError("");
    sessionMessagesApi
      .list(sessionKey, { limit: PAGE, kinds })
      .then((r) => {
        if (!alive) return;
        setItems(r.items || []);
        setTotal(r.total || 0);
        setHasMore(!!r.hasMore);
      })
      .catch((e) => {
        if (!alive) return;
        setError(String(e?.message || e));
        setItems([]);
        setTotal(0);
        setHasMore(false);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [sessionKey, kinds, tick]);

  /* [2026-11-14 12:33 Feng] "newest at the bottom, older above" —— after initial load / refresh / session switch
     scroll to the bottom, so the newest row is visible on landing.
     ★ Scroll only after a "batch replace"; `loadMore` (prepending older rows) does not scroll, otherwise the
     user gets yanked away from the bottom. */
  React.useEffect(() => {
    const el = listRef.current;
    if (!el || loading || loadingMore) return;
    el.scrollTop = el.scrollHeight;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, sessionKey, tick]);

  const loadMore = React.useCallback(async () => {
    if (!sessionKey || !items.length) return;
    const el = listRef.current;
    /* loading earlier pushes existing content down — record scrollHeight before loading and add the delta after,
        so clicking "earlier" does not bounce the view to the bottom. */
    const prevHeight = el?.scrollHeight ?? 0;
    const prevTop = el?.scrollTop ?? 0;
    setLoadingMore(true);
    try {
      const r = await sessionMessagesApi.list(sessionKey, {
        limit: PAGE,
        /* [2026-11-14 13:05] Cursor changed from pkid to the (msg_at, pkid) composite:
           the collector rebuilt agx_user_message, all pkids were re-assigned, and back-filled historical rows
           ended up with larger pkids (measured 233 places where pkid is in reverse time order) —
           relying on pkid alone drops rows and skips rows. */
        before: items[0].id,
        beforeAt: items[0].atIso,
        kinds,
      });
      setItems((prev) => [...(r.items || []), ...prev]);
      setHasMore(!!r.hasMore);
      requestAnimationFrame(() => {
        const box = listRef.current;
        if (!box) return;
        box.scrollTop = box.scrollHeight - prevHeight + prevTop;
      });
    } catch (e) {
      setError(String((e as Error)?.message || e));
    } finally {
      setLoadingMore(false);
    }
  }, [sessionKey, items, kinds]);

  return (
    <section
      className={cn(
        "flex min-w-0 flex-col gap-1.5 rounded-md border bg-muted/20 p-2",
        fill && "min-h-0 flex-1",
        className,
      )}
      data-testid="uib-layer-history"
    >
      {/* Title row: visually aligned with the LayerTitle of ①-⑦, but **takes no number** —
          the numbers are "the seven steps of the send flow"; history is a read-only side path, and giving it
          a number would make people think it is a required step. */}
      <div className="flex min-w-0 shrink-0 items-center gap-2">
        <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-foreground">
          <History className="h-3.5 w-3.5 text-muted-foreground" />
          {t("sessionHistory.history.title")}
        </span>
        <span className="min-w-0 truncate text-[11px] text-muted-foreground" data-testid="uib-history-meta">
          {sessionKey
            ? t("sessionHistory.history.meta", {
                total,
                loaded: items.length < total ? t("sessionHistory.history.metaLoaded", { n: items.length }) : "",
                updated: autoAt ? t("sessionHistory.history.metaUpdated", { at: autoAt }) : "",
              })
            : t("sessionHistory.history.metaNoSession")}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={() => setTick((v) => v + 1)}
            disabled={!sessionKey || loading}
            className="rounded p-1 text-muted-foreground hover:text-foreground disabled:opacity-40"
            title={t("sessionHistory.history.refreshTitle")}
            data-testid="uib-history-refresh"
          >
            <RefreshCw className={cn("h-3 w-3", loading && "animate-spin")} />
          </button>
        </span>
      </div>

      {!sessionKey ? (
        <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <Info className="h-3 w-3 shrink-0" />
          {t("sessionHistory.history.newSession")}
        </p>
      ) : error ? (
        <p className="text-[11px] text-destructive" data-testid="uib-history-error">
          {t("sessionHistory.history.loadFailed", { msg: error })}
        </p>
      ) : loading ? (
        <p className="flex items-center gap-1.5 py-2 text-[11px] text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t("sessionHistory.history.loading")}
        </p>
      ) : items.length === 0 && mine.length === 0 ? (
        <p className="text-[11px] text-muted-foreground" data-testid="uib-history-empty">
          {t("sessionHistory.history.empty", {
            name: sessionName ? t("sessionHistory.history.emptyName", { name: sessionName }) : "",
          })}
        </p>
      ) : (
        <>
          {/* [2026-11-14 12:33 Feng] "load earlier" **moves to the top** ——
              now older is above and newest at the bottom (like a normal AI interface),
              so to keep looking further back the action belongs in the header, not stuck at the bottom
              competing for space with "latest". */}
          {hasMore && (
            <button
              type="button"
              onClick={loadMore}
              disabled={loadingMore}
              className="shrink-0 self-center rounded px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-40"
              data-testid="uib-history-more"
            >
              {loadingMore ? t("sessionHistory.history.loadingMore") : t("sessionHistory.history.loadMore")}
            </button>
          )}
          {/* inner height cap + inner scrolling: history is side information and must not push "send" out of sight.
              In fill mode height is delegated to the parent (moved above the body it must consume the remaining space). */}
          <div
            ref={listRef}
            className={cn(
              "flex min-h-0 flex-col gap-2 overflow-y-auto pr-0.5",
              fill ? "flex-1" : "max-h-[240px]",
            )}
            data-testid="uib-history-list"
          >
            {items.map((m) =>
              m.kind === "envelope" ? (
                <article key={m.id} className="flex flex-col gap-1.5" data-testid={`uib-history-item-${m.id}`}>
                  <EnvelopeRow m={m} onOpen={() => setDetail(m)} />
                  {/* [2026-11-16 14:09] Envelope rows used to **draw only the instruction, not the answer** ——
                      while `reply_content` for envelope rows does exist in the DB (342/372 in the last 7 days).
                      That is the main cause of "after sending I only see the instruction, not the agent reply". */}
                  <ReplyBlock m={m} />
                </article>
              ) : m.kind !== "human" ? (
                /* [2026-11-14 20:05] system / notice / command kinds: **must not be dressed up as "a question I asked"**.
                   Previously every kind went through the bottom "question right · answer left" layout, so
                   `[OpenClaw heartbeat]` showed as a blue bubble pinned right — looking like Feng said it.
                   Centered, dashed box, no bubble direction is the correct semantics for system messages. */
                <article key={m.id} data-testid={`uib-history-item-${m.id}`}>
                  <div className="flex flex-col items-center gap-1">
                    <div className="flex flex-wrap items-center justify-center gap-1.5 text-[10px] text-muted-foreground">
                      <span className="rounded bg-muted px-1">{t(KIND_KEY[m.kind])}</span>
                      <span className="font-mono">{fmtAt(m.at)}</span>
                      {m.replyParts > 1 && <span>{t("sessionHistory.reply.toolCalls", { n: m.replyParts })}</span>}
                    </div>
                    <div className="w-full max-w-[94%] rounded-md border border-dashed bg-muted/30 px-2.5 py-1.5">
                      <CollapsibleText
                        text={m.content}
                        tone="a"
                        testId={`uib-history-q-${m.id}`}
                      />
                    </div>
                  </div>
                  {/* [2026-11-16 14:09] system / notice / command rows are also "question and answer"
                      (measured over the last 7 days: of 192 system rows, 135 have reply_content; notice 47/48),
                      while previously only the question was shown. The answer is not a "system broadcast",
                      so it gets its own left-aligned block. */}
                  <div className="mt-1 flex justify-start">
                    <ReplyBlock m={m} />
                  </div>
                </article>
              ) : (
              <article key={m.id} className="flex flex-col gap-1.5" data-testid={`uib-history-item-${m.id}`}>
                {/* ── my (human) question: right-aligned ──
                    [2026-11-14 12:33 Feng] "put my question right-aligned, leave a gap in the middle, use different colors"
                    ★ use `self-end` + `max-w-[85%]`: the bubble hugs the right but leaves space on the left,
                      so it is obvious "who is talking", and a long text does not fill the whole row. */}
                <div className="flex flex-col items-end gap-0.5">
                  <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    {m.kind !== "human" && (
                      <span className="rounded bg-muted px-1">{t(KIND_KEY[m.kind])}</span>
                    )}
                    {/* [2026-11-17 08:50] message queued while busy: strip the English wrapper, but mark "it was processed late" ——
                        unmarked, people think what they said was seen immediately (it actually waited minutes).
                        `title` carries the full sentence, so "sent while busy" is not too cryptic. */}
                    {splitQueued(m.content).queued && (
                      <span
                        className="rounded border border-border bg-muted px-1 text-[10px]"
                        title={t("sessionHistory.history.queuedHint")}
                        data-testid={`uib-history-queued-${m.id}`}
                      >
                        {t("sessionHistory.history.queued")}
                      </span>
                    )}
                    <span className="font-mono">{fmtAt(m.at)}</span>
                  </div>
                  <div className="max-w-[80%] rounded-lg rounded-tr-sm border border-primary/30 bg-primary/10 px-3 py-2 text-foreground">
                    <CollapsibleText
                      text={splitQueued(m.content).text}
                      tone="q"
                      testId={`uib-history-q-${m.id}`}
                    />
                  </div>
                </div>

                {/* ── agent answer: left-aligned ──
                    ★ Use card (light) as background, not primary (heavy): answers are far longer than
                      questions, and a large area of heavy color makes the right column noisy.
                    ★ Shares `ReplyBlock` with envelope / system messages (see its comment: one missed change
                      and "this kind of message has an answer, that kind does not"). */}
                <ReplyBlock m={m} />
              </article>
              ),
            )}

            {/* ── local message pending persistence (2026-11-14 20:31 Feng)──────────────────
                Shown immediately at the **end of the list** after sending (= newest position), no separate box below.
                Grey dashed = "I know it is not in the DB yet"; once stored this one disappears and the real row stays.
                ★ `self-end`, same side as "my question": semantically it is the line I said. */}
            {mine.map((p) => (
              <article key={p.key} data-testid={`uib-pending-${p.key}`}>
                <div className="flex flex-col items-end gap-0.5">
                  <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                    <span
                      className={cn(
                        "rounded px-1",
                        p.status === "waiting"
                          ? "bg-muted text-muted-foreground"
                          : p.status === "slow"
                            ? "bg-muted text-muted-foreground/80"
                            : "bg-warning/15 text-warning",
                      )}
                    >
                      {p.status === "waiting"
                        ? t("sessionHistory.pending.waiting")
                        : p.status === "slow"
                          ? t("sessionHistory.pending.slow")
                          : t("sessionHistory.pending.timeout")}
                      {p.status !== "timeout" && (
                        <Loader2 className="ml-0.5 inline h-2.5 w-2.5 animate-spin align-[-1px]" />
                      )}
                    </span>
                    {p.mode !== "text" && (
                      <span className="rounded bg-muted px-1">
                        {p.mode === "task" ? t("sessionHistory.pending.modeTask") : p.mode === "quickcmd" ? t("sessionHistory.pending.modeQuickCmd") : p.mode}
                      </span>
                    )}
                    {p.note && <span className="truncate">{p.note}</span>}
                    {/* [2026-11-17 15:41 Feng] the skills block sits at the **end** of the body and is invisible when collapsed
                        (cut at 240 chars). Show a badge so it is clear without expanding that this message went out
                        with a skills note. */}
                    {p.text.includes("【本次执行需参考的技能") && (
                      <span
                        className="shrink-0 rounded bg-chart-4/15 px-1 text-chart-4"
                        title={t("sessionHistory.pending.hasSkillsHint")}
                        data-testid="uib-pending-skills"
                      >
                        {t("sessionHistory.pending.hasSkills")}
                      </span>
                    )}
                    {/* [2026-11-14 21:12 Feng] In task mode this shows **the instruction the kernel really dispatched**,
                        so print the character count to make clear at a glance that it is not the same thing as
                        the short instruction in the input box above. */}
                    {p.runUuid && !p.loadingReal && (
                      <span className="rounded bg-primary/10 px-1 text-primary">
                        {t("sessionHistory.pending.realLen", { n: p.realLen || p.text.length })}
                      </span>
                    )}
                    {p.runUuid && p.loadingReal && (
                      <span className="inline-flex items-center gap-0.5 text-muted-foreground">
                        <Loader2 className="h-2.5 w-2.5 animate-spin" />
                        {t("sessionHistory.pending.readingReal")}
                      </span>
                    )}
                    <span className="font-mono">
                      {new Date(p.at).toLocaleTimeString("zh-CN", { hour12: false })}
                    </span>
                  </div>
                  {/* [2026-11-17 15:41 Feng] "The page does not show the content with skills, right after sending there is no update."
                      → the echoed content now uses the full text returned by the backend (including the
                        reference-skills block), and the hard 400-character cut becomes expandable: the skills
                        block is at the **end** of the body, so truncation cuts exactly it first — that fixes
                        sending but not "being able to see it". */}
                  <div className="max-w-[80%] rounded-lg rounded-tr-sm border border-dashed border-primary/50 bg-primary/10 px-3 py-2">
                    <CollapsibleText
                      text={p.text || t("sessionHistory.pending.readingText")}
                      tone="q"
                      testId={`uib-pending-text-${p.key}`}
                    />
                  </div>
                </div>
              </article>
            ))}
          </div>
        </>
      )}

      <EnvelopeDetailDialog m={detail} onClose={() => setDetail(null)} />
    </section>
  );
  },
);

export default SessionHistory;
