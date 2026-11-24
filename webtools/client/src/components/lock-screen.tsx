/**
 * LockScreen — lock overlay (mirrors vue-admin-tpl "LockDialog + LockPage")
 * ────────────────────────────────────────────────────────────────────────────
 * Flow:
 *   1) click "Lock screen" → open LockDialog and enter a lock password
 *   2) after confirming → show the full-screen lock layer (LockPage), the same password unlocks it
 * Front-end demo only: the password lives in component state.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";

type LockCtx = {
  /** Opens the "lock screen" password dialog */
  requestLock: () => void;
};

const Ctx = React.createContext<LockCtx | null>(null);

export function useLockScreen(): LockCtx {
  const ctx = React.useContext(Ctx);
  if (!ctx) throw new Error("useLockScreen must be used within <LockScreenProvider>");
  return ctx;
}

export function LockScreenProvider({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation();
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [lockPwd, setLockPwd] = React.useState("");
  const [locked, setLocked] = React.useState(false);
  const [unlockInput, setUnlockInput] = React.useState("");
  const [err, setErr] = React.useState("");

  const requestLock = React.useCallback(() => {
    setLockPwd("");
    setDialogOpen(true);
  }, []);

  const confirmLock = () => {
    if (!lockPwd.trim()) {
      setErr(t("miscShell.lockScreen.needPassword"));
      return;
    }
    setErr("");
    setDialogOpen(false);
    setLocked(true);
    setUnlockInput("");
  };

  const unlock = () => {
    if (unlockInput === lockPwd) {
      setLocked(false);
      setErr("");
    } else {
      setErr(t("miscShell.lockScreen.wrongPassword"));
    }
  };

  return (
    <Ctx.Provider value={{ requestLock }}>
      {children}

      {/* Lock password input dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent data-testid="lock-dialog" className="max-w-[420px]">
          <DialogHeader>
            <DialogTitle>{t("miscShell.lockScreen.title")}</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col items-center gap-3 py-2">
            <div className="flex h-16 w-16 items-center justify-center rounded-full bg-gradient-to-br from-sky-400 via-indigo-500 to-fuchsia-500 text-2xl font-semibold text-white">
              F
            </div>
            <span className="text-sm text-muted-foreground">feng</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="lock-pwd">{t("miscShell.lockScreen.password")}</Label>
            <Input
              id="lock-pwd"
              type="password"
              autoFocus
              value={lockPwd}
              onChange={(e) => setLockPwd(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && confirmLock()}
              data-testid="lock-pwd"
            />
            {err && <p className="text-sm text-destructive">{err}</p>}
          </div>
          <DialogFooter>
            <Button onClick={confirmLock} data-testid="lock-confirm">
              {t("miscShell.lockScreen.lock")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Full-screen lock layer */}
      {locked && (
        <div
          data-testid="lock-page"
          className="fixed inset-0 z-toast flex flex-col items-center justify-center gap-5 bg-background/95 backdrop-blur-xl"
        >
          <div className="flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-br from-sky-400 via-indigo-500 to-fuchsia-500 text-3xl font-semibold text-white">
            F
          </div>
          <span className="text-base font-medium">feng</span>
          <div className="flex w-64 flex-col gap-2">
            <Input
              type="password"
              autoFocus
              placeholder={t("miscShell.lockScreen.unlockPlaceholder")}
              value={unlockInput}
              onChange={(e) => setUnlockInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && unlock()}
              data-testid="unlock-pwd"
            />
            {err && (
              <p className="text-center text-sm text-destructive" data-testid="unlock-err">
                {err}
              </p>
            )}
            <Button className="w-full" onClick={unlock} data-testid="unlock-btn">
              {t("miscShell.lockScreen.unlock")}
            </Button>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
