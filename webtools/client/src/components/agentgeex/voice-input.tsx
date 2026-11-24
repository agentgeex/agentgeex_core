/**
 * Voice input — speak → text, delivered into the caller's input box.
 *
 * ## Why a standalone component
 * "Turn speech into text" is needed in more than one place (send-message dialog, session page messages…),
 * but **there is only one correct way to record audio**: permission, codec format, timeout, track cleanup, error attribution.
 * Written separately in each place, one of them will inevitably forget `track.stop()` (mic indicator stays on) or forget to cancel.
 * So: the recording logic lives only here, and callers hook up just one `onText`.
 *
 * ## Key trade-offs
 * 1. **Record then upload; no live transcription while speaking.** The target scenario is "write one sentence in the input box";
 *    streaming gives no benefit for a single sentence and adds jitter ("half a sentence on screen").
 * 2. **The transcribed text is appended at the end, never overwriting existing content.** The person may have already typed
 *    something; overwriting would delete it — that is the least acceptable failure mode.
 * 3. **Failures are not rewritten or swallowed**: state clearly whether it is "no permission / no device / service off / timeout",
 *    because those four need completely different actions from the person.
 * 4. **Auto-stop after 60 seconds.** Forget to press stop and it keeps recording, then uploads 10 minutes of audio (and times out);
 *    better to wrap up automatically at the deadline.
 * 5. **An oscilloscope while recording.** Not for looks: the two most common recording failures are "mic muted by the system"
 *    and "forgot to press stop", and both show the same symptom: "I talked for ages, why no text".
 *    A waveform that really moves tells the person at a glance "did sound go in".
 * 6. **Must come back by itself when the service drops.** The BFF probes health in the background every 8s; here we only read
 *    its conclusion and follow it, never reconnect inside the browser (there is no long connection to connect to).
 */

import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2, Mic, Square, WifiOff, X } from "lucide-react";

import { audioApi } from "@/providers/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { apiErrorText } from "@/lib/api-error";

/** Recording limit (seconds): at this point auto-stop and start transcribing */
const MAX_SECONDS = 60;

/** Hint key when the service is unavailable (text in the language pack). It disappears on its own once the state recovers — no manual clearing. */
const SVC_DOWN_KEY = "voiceInput.svcDown";

/** Frontend polling cadence for the service state (the BFF probes every 8s itself; here we read its conclusion, which is cheap) */
const SVC_POLL_MS = 8000;

/** Recording state machine. Exported for callers — they use it to decide "can send be clicked now". */
export type VoicePhase = "idle" | "asking" | "recording" | "transcribing" | "error";
type Phase = VoicePhase;

/** During these two phases the body is not final: the speaking part is still in the mic, and the transcribing part has not
 *  landed in the input box. Allowing send here would send a body missing the sentence just spoken — while the person thinks it was said. */
export function voiceBlocksSend(p: VoicePhase): boolean {
  return p === "recording" || p === "transcribing";
}

/** Pick a codec the current browser really supports. Safari has no webm, fall back to mp4; the server's ffmpeg handles both. */
function pickMime(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  const cands = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ];
  return cands.find((t) => {
    try {
      return MediaRecorder.isTypeSupported(t);
    } catch {
      return false;
    }
  });
}

function fmt(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * Oscilloscope — shows sound fluctuation while recording.
 *
 * ## Why bars instead of a continuous waveform
 * A continuous waveform blurs into one blob in a 60px-wide strip (people cannot tell "is there sound").
 * Drawing bars by frequency band gives a row of low flat stumps when silent and a few bars jumping in the middle when
 * speaking — **high contrast, readable at a glance**.
 *
 * ## Why not draw a "spectrum" from AnalyserNode's frequency data
 * Speech energy concentrates in low frequencies; a pure spectrum only ever moves one or two bars on the left, looking broken.
 * Here the frequency data is **clamped at both ends**: the first few bins (DC / very low frequency noise) are dropped,
 * and each bar takes the peak of an interval — visually closer to "the whole row moving while a person speaks".
 */
function Scope({ analyserRef }: { analyserRef: React.RefObject<AnalyserNode | null> }) {
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const rafRef = React.useRef<number | null>(null);
  /** Smoothed bar height (0..1) so the bars "jump" instead of "flash" */
  const barsRef = React.useRef<number[]>([]);
  const [level, setLevel] = React.useState(0);

  React.useEffect(() => {
    const BARS = 22;
    if (barsRef.current.length !== BARS) barsRef.current = new Array(BARS).fill(0);

    /* Device pixel ratio: without handling it, it blurs into mosaic on Retina */
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const draw = () => {
      const cv = canvasRef.current;
      const an = analyserRef.current;
      if (!cv || !an) {
        rafRef.current = requestAnimationFrame(draw);
        return;
      }
      const w = cv.clientWidth || 72;
      const h = cv.clientHeight || 22;
      if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
        cv.width = Math.round(w * dpr);
        cv.height = Math.round(h * dpr);
      }
      const ctx = cv.getContext("2d");
      if (!ctx) return;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const bins = new Uint8Array(an.frequencyBinCount);
      an.getByteFrequencyData(bins);
      /* Drop the lowest 2 bins (DC offset + very low frequency), take only the front 3/4 that carries speech energy */
      const from = 2;
      const to = Math.max(from + BARS, Math.floor(bins.length * 0.75));
      const step = (to - from) / BARS;

      const bars = barsRef.current;
      let peak = 0;
      for (let i = 0; i < BARS; i += 1) {
        let v = 0;
        const a = Math.floor(from + i * step);
        const b = Math.max(a + 1, Math.floor(from + (i + 1) * step));
        for (let k = a; k < b && k < bins.length; k += 1) v = Math.max(v, bins[k]);
        /* Map to 0..1: normalize by 230 first (leave a margin under the 255 full scale), then raise to the 0.75 power.
           Why the power: frequency byte values relate to what ears perceive exponentially; a linear mapping keeps normal
           speech glued to the bottom and only shouting jumps up — looking broken. After it, quiet speech still undulates. */
        const target = Math.min(1, Math.pow(v / 230, 0.75));
        /* Rise fast, fall slow: sound is transient; equal smoothing makes it look sluggish */
        bars[i] += (target - bars[i]) * (target > bars[i] ? 0.55 : 0.18);
        peak = Math.max(peak, target);
      }

      const bw = Math.max(2, (w - (BARS - 1) * 1.5) / BARS);
      for (let i = 0; i < BARS; i += 1) {
        const bh = Math.max(1.5, bars[i] * (h - 2));
        const x = i * (bw + 1.5);
        const y = (h - bh) / 2; // Symmetric top and bottom, like a "vocal cord"
        ctx.fillStyle = bars[i] > 0.06 ? "rgb(239 68 68)" : "rgb(239 68 68 / 0.35)";
        const rr = (ctx as CanvasRenderingContext2D & {
          roundRect?: (x: number, y: number, w: number, h: number, r: number) => void;
        }).roundRect;
        if (rr) {
          ctx.beginPath();
          rr.call(ctx, x, y, bw, bh, Math.min(1.5, bw / 2));
          ctx.fill();
        } else {
          ctx.fillRect(x, y, bw, bh); /* Old Safari has no roundRect */
        }
      }

      /* For the acceptance script to read: actually moving means the media channel is alive */
      setLevel(Number(peak.toFixed(3)));
      rafRef.current = requestAnimationFrame(draw);
    };

    rafRef.current = requestAnimationFrame(draw);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
  }, [analyserRef]);

  return (
    <canvas
      ref={canvasRef}
      className="h-[22px] w-[72px] shrink-0"
      data-testid="voice-scope"
      data-level={level}
      aria-hidden="true"
    />
  );
}

export interface VoiceInputProps {
  /** Receives the recognized text. The caller decides where to append it and how to join it. */
  onText: (text: string) => void;
  disabled?: boolean;
  /** When initially true it is not shown (e.g. disabled by a feature config) */
  hidden?: boolean;
  className?: string;
  /** Report phase changes. Callers use it to decide "cannot send while recording/transcribing is unfinished" —
   *  recording lives only inside this component and is invisible outside, so it must speak up. */
  onPhaseChange?: (phase: VoicePhase) => void;
  /** data-testid prefix, default "voice".
   *  ★ Why it must be configurable: the send-message dialog mounts **two at once** (main box + enlarged window);
   *    with "voice-start" for both, automation would match two elements at once (Playwright strict mode fails),
   *    and people could not tell which is which from the DOM either. */
  testIdPrefix?: string;
}

export function VoiceInput({
  onText,
  disabled,
  hidden,
  className,
  onPhaseChange,
  testIdPrefix = "voice",
}: VoiceInputProps) {
  /* ★ This used to be called t too (as the testid prefix). After i18n, t must be reserved for the translate function →
     renamed to tid; every data-testid={t("...")} below was switched to tid without exception. */
  const { t } = useTranslation();
  const tid = (n: string) => `${testIdPrefix}-${n}`;
  const [phase, setPhase] = React.useState<Phase>("idle");
  const [secs, setSecs] = React.useState(0);
  const [msg, setMsg] = React.useState("");
  /** Voice service availability (reads the BFF background probe's conclusion; "unknown" = not obtained yet) */
  const [svc, setSvc] = React.useState<"unknown" | "up" | "down">("unknown");

  const recRef = React.useRef<MediaRecorder | null>(null);
  const streamRef = React.useRef<MediaStream | null>(null);
  const chunksRef = React.useRef<Blob[]>([]);
  const timerRef = React.useRef<number | null>(null);
  /** Cancel flag: when true, onstop does not upload (the person gave up) */
  const cancelRef = React.useRef(false);
  /** Audio graph (for the live waveform). Exists only while recording, closed as soon as recording stops. */
  const audioCtxRef = React.useRef<AudioContext | null>(null);
  const analyserRef = React.useRef<AnalyserNode | null>(null);

  const releaseMic = React.useCallback(() => {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
    /* ★ Every track must be stopped explicitly: otherwise the mic indicator in the browser's top bar stays on and
       users think the app is eavesdropping. This is the easiest step to forget in a recording feature. */
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    /* Close the audio graph too: an unclosed AudioContext keeps holding the audio hardware, which on Mac shows as
       "recording stopped but the system volume icon still shows the mic in use". */
    analyserRef.current?.disconnect();
    analyserRef.current = null;
    void audioCtxRef.current?.close().catch(() => {});
    audioCtxRef.current = null;
  }, []);

  /* Phase reporting: keep the callback in a ref so a new function on each parent render does not re-run the effect. */
  const onPhaseRef = React.useRef(onPhaseChange);
  onPhaseRef.current = onPhaseChange;
  React.useEffect(() => {
    onPhaseRef.current?.(phase);
  }, [phase]);

  /* Clean up on unmount — closing the dialog while recording is a common path */
  React.useEffect(() => {
    return () => {
      cancelRef.current = true;
      try {
        recRef.current?.state === "recording" && recRef.current.stop();
      } catch {
        /* Already stopped */
      }
      releaseMic();
      /* ★ Send one more idle on unmount: otherwise the caller keeps a "recording" lock, and next time this dialog opens,
          the send button is grey while the person is not recording at all. */
      onPhaseRef.current?.("idle");
    };
  }, [releaseMic]);

  /* Follow the service state — only read the BFF probe's conclusion, never reconnect in the browser.
     ★ Why not "reconnect" on the frontend: this link opens a fresh WS per recognition, there is no persistent connection
       to reconnect to. What really must come back by itself is the [server → ASR service] segment, which the BFF background
       probe covers; the frontend only needs to know "is it usable now", and to drop the hint the moment it recovers. */
  React.useEffect(() => {
    let alive = true;
    let timer: number | null = null;

    const tick = async () => {
      try {
        const r = await audioApi.health();
        if (!alive) return;
        setSvc(r.state === "up" ? "up" : r.state === "down" ? "down" : "unknown");
      } catch {
        if (alive) setSvc("down");
      }
      if (alive) timer = window.setTimeout(() => void tick(), SVC_POLL_MS);
    };

    void tick();
    return () => {
      alive = false;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, []);

  const upload = React.useCallback(
    async (blob: Blob) => {
      if (!blob.size) {
        setPhase("error");
        setMsg(t("voiceInput.noAudio"));
        return;
      }
      setPhase("transcribing");
      setMsg("");
      try {
        const r = await audioApi.transcribe(blob);
        const text = (r.text || "").trim();
        setPhase("idle");
        if (!text) {
          setMsg(t("voiceInput.noText"));
          return;
        }
        onText(text);
      } catch (e) {
        const err = e as { code?: string; message?: string; status?: number };
        /* Four causes → four sentences. Merging them into one "transcription failed" leaves people guessing. */
        const map: Record<string, string> = {
          EMPTY_AUDIO: t("voiceInput.noAudio"),
          AUDIO_TOO_LARGE: t("voiceInput.tooLarge"),
          ASR_UNAVAILABLE: t("voiceInput.asrUnavailable"),
          ASR_TIMEOUT: t("voiceInput.asrTimeout"),
          ASR_FAILED: t("voiceInput.asrFailed"),
        };
        setPhase("error");
        setMsg(map[err.code ?? ""] || (err.message ? apiErrorText(e, t) : t("voiceInput.transcribeFailed")));
      }
    },
    [onText]
  );

  const stop = React.useCallback(() => {
    try {
      recRef.current?.stop();
    } catch {
      /* Already stopped */
    }
  }, []);

  const start = React.useCallback(async () => {
    setMsg("");
    setPhase("asking");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      });
    } catch (e) {
      const name = (e as { name?: string })?.name ?? "";
      setPhase("error");
      setMsg(
        name === "NotAllowedError"
          ? t("voiceInput.micDenied")
          : name === "NotFoundError"
            ? t("voiceInput.micNotFound")
            : t("voiceInput.micOpenFailed", { reason: name || t("voiceInput.unknownReason") })
      );
      return;
    }

    streamRef.current = stream;
    chunksRef.current = [];
    cancelRef.current = false;

    /* Data source for the oscilloscope: MediaStream → AnalyserNode.
       ★ Do not connect to destination (it would listen to itself and become feedback howling). Analyze only, no sound. */
    try {
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ac = new AC();
      const src = ac.createMediaStreamSource(stream);
      const an = ac.createAnalyser();
      an.fftSize = 512;
      an.smoothingTimeConstant = 0.6;
      src.connect(an);
      audioCtxRef.current = ac;
      analyserRef.current = an;
    } catch {
      /* No audio analysis does not affect recording itself — degrade to "no waveform", do not block the feature */
      analyserRef.current = null;
    }

    const mime = pickMime();
    let rec: MediaRecorder;
    try {
      rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    } catch {
      releaseMic();
      setPhase("error");
      setMsg(t("voiceInput.unsupported"));
      return;
    }
    recRef.current = rec;

    rec.ondataavailable = (ev) => {
      if (ev.data && ev.data.size) chunksRef.current.push(ev.data);
    };
    rec.onstop = () => {
      releaseMic();
      const blob = new Blob(chunksRef.current, { type: mime || "audio/webm" });
      chunksRef.current = [];
      if (cancelRef.current) {
        cancelRef.current = false;
        setPhase("idle");
        setSecs(0);
        return;
      }
      setSecs(0);
      void upload(blob);
    };

    rec.start();
    setSecs(0);
    setPhase("recording");
    timerRef.current = window.setInterval(() => {
      setSecs((s) => {
        const n = s + 1;
        if (n >= MAX_SECONDS) {
          /* Auto wrap up at the deadline: stop the timer first so onstop does not fire again; after stop() the onstop path uploads */
          stop();
          return MAX_SECONDS;
        }
        return n;
      });
    }, 1000);
  }, [releaseMic, stop, upload]);

  const cancel = React.useCallback(() => {
    cancelRef.current = true;
    stop();
    setMsg("");
  }, [stop]);

  if (hidden) return null;

  const busy = phase === "asking" || phase === "transcribing";
  const recording = phase === "recording";

  return (
    <div className={cn("flex items-center gap-1.5", className)} data-testid={tid("input")}>
      {recording ? (
        <>
          <span
            className="flex items-center gap-1.5 rounded-full bg-destructive/10 px-2 py-1 text-xs font-medium text-destructive"
            data-testid={tid("recording")}
          >
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-destructive" />
            {t("voiceInput.recording", { time: fmt(secs) })}
            {/* Oscilloscope: whether sound goes in, look here. Silent = a row of low stumps, speaking = the middle jumps up. */}
            <Scope analyserRef={analyserRef} />
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 gap-1 px-2 text-xs"
            onClick={stop}
            data-testid={tid("stop")}
            title={t("voiceInput.stopTitle")}
          >
            <Square className="h-3 w-3" />{t("voiceInput.done")}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-7 w-7 p-0 text-muted-foreground"
            onClick={cancel}
            data-testid={tid("cancel")}
            title={t("voiceInput.cancelTitle")}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </>
      ) : (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 gap-1 px-2 text-xs text-muted-foreground hover:text-foreground"
          disabled={disabled || busy}
          onClick={() => void start()}
          data-testid={tid("start")}
          title={t("voiceInput.startTitle", { sec: MAX_SECONDS })}
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
          {phase === "asking"
            ? t("voiceInput.asking")
            : phase === "transcribing"
              ? t("voiceInput.transcribing")
              : t("voiceInput.label")}
        </Button>
      )}

      {(() => {
        /* Report the concrete failure reason if there is one; otherwise report "reconnecting" when the service is unavailable.
           Note it is **computed**, not stored: as soon as the service recovers this becomes empty automatically,
           nobody needs to clear that message. */
        const hint = msg || (svc === "down" ? t(SVC_DOWN_KEY) : "");
        if (!hint) return null;
        const isErr = phase === "error" || svc === "down";
        return (
          <span
            className={cn("flex items-center gap-1 text-xs", isErr ? "text-warning" : "text-muted-foreground")}
            data-testid={tid("msg")}
            data-svc={svc}
          >
            {svc === "down" ? <WifiOff className="h-3 w-3 shrink-0" /> : <AlertTriangle className="h-3 w-3 shrink-0" />}
            {hint}
          </span>
        );
      })()}
    </div>
  );
}
