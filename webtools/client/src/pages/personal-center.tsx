/**
 * Personal center (/personal/center)
 *
 * ★ 2026-11-18 fixed two places that "look usable but nothing happens" (self-check before open sourcing):
 *   ① the page had a hard-coded sample identity (name / phone / email / role) —
 *      that was demo data from the template stage, unrelated to the person logged in, and misleading.
 *      Now it shows only **the real identity in the session** (account / nickname), and fields with no source
 *      honestly show "—".
 *   ② "Save" and "Confirm changes" used to toast "updated successfully", but **no request was ever sent**.
 *      Fake success is worse than doing nothing: the user believes the password really changed.
 *      Now it says honestly "this feature is not wired to a backend yet".
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { getUser } from "@/providers/api";

/** user info returned by BFF login (fields may be missing, do not assume they exist) */
interface SessionUser {
  id?: string;
  name?: string;
  role?: string;
  email?: string;
}

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{children}</span>
    </div>
  );
}

export default function PersonalCenterPage() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [active, setActive] = React.useState("basic");

  const user = React.useMemo(() => (getUser<SessionUser>() || {}) as SessionUser, []);
  const account = user.id || "—";
  const initial = (user.name || user.id || "?").slice(0, 1).toUpperCase();

  const [realName, setRealName] = React.useState(user.name || "");
  const [email, setEmail] = React.useState(user.email || "");

  const [pwd, setPwd] = React.useState("");
  const [npwd, setNpwd] = React.useState("");
  const [npwd2, setNpwd2] = React.useState("");
  const [pwdErr, setPwdErr] = React.useState("");

  /* neither save action has a backend endpoint yet — say so honestly, do not fake success */
  const notConnected = () =>
    toast({ title: t("profile.notConnectedTitle"), description: t("profile.notConnectedDesc") });

  const saveBasic = () => {
    if (!realName.trim()) return toast({ title: t("profile.nameRequired"), variant: "destructive" });
    notConnected();
  };

  const savePwd = () => {
    if (!pwd || !npwd || !npwd2) {
      setPwdErr(t("profile.fillAll"));
      return;
    }
    if (npwd !== npwd2) {
      setPwdErr(t("profile.mismatch"));
      return;
    }
    setPwdErr("");
    notConnected();
  };

  return (
    <div className="flex flex-col gap-4" data-testid="personal-center">
      <div>
        <h1 className="text-lg font-semibold">{t("profile.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("profile.subtitle")}</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[380px_1fr]">
        {/* personal info card */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("profile.infoCard")}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex justify-center py-2">
              <div
                data-testid="pc-avatar"
                className="flex h-[120px] w-[120px] items-center justify-center rounded-full bg-gradient-to-br from-info via-chart-5 to-chart-4 text-4xl font-semibold text-white"
                title={t("profile.avatarHint")}
              >
                {initial}
              </div>
            </div>
            <Separator />
            <InfoRow label={t("profile.account")}>{account}</InfoRow>
            <Separator />
            <InfoRow label={t("profile.nickname")}>{user.name || "—"}</InfoRow>
            <Separator />
            <InfoRow label={t("profile.email")}>{user.email || "—"}</InfoRow>
            <Separator />
            <div className="flex items-center justify-between py-2 text-sm">
              <span className="text-muted-foreground">{t("profile.role")}</span>
              <span className="flex gap-1">
                {user.role ? <Badge variant="info">{user.role}</Badge> : <span className="text-muted-foreground">—</span>}
              </span>
            </div>
            <Separator />
          </CardContent>
        </Card>

        {/* basic profile */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("profile.basicCard")}</CardTitle>
          </CardHeader>
          <CardContent>
            <Tabs value={active} onValueChange={setActive}>
              <TabsList>
                <TabsTrigger value="basic" data-testid="pc-tab-basic">
                  {t("profile.tabBasic")}
                </TabsTrigger>
                <TabsTrigger value="pwd" data-testid="pc-tab-pwd">
                  {t("profile.tabPassword")}
                </TabsTrigger>
              </TabsList>

              <TabsContent value="basic" className="pt-4">
                <div className="flex max-w-md flex-col gap-4">
                  <div className="grid gap-3 sm:grid-cols-[8em_1fr] sm:items-center">
                    <Label htmlFor="pc-name" className="text-right">{t("profile.nickname")}</Label>
                    <Input id="pc-name" value={realName} onChange={(e) => setRealName(e.target.value)} />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[8em_1fr] sm:items-center">
                    <Label htmlFor="pc-email" className="text-right">{t("profile.email")}</Label>
                    <Input id="pc-email" value={email} onChange={(e) => setEmail(e.target.value)} />
                  </div>
                  <Button onClick={saveBasic} data-testid="pc-save-basic">
                    {t("common.save")}
                  </Button>
                </div>
              </TabsContent>

              <TabsContent value="pwd" className="pt-4">
                <div className="flex max-w-md flex-col gap-4">
                  <div className="grid gap-3 sm:grid-cols-[8em_1fr] sm:items-center">
                    <Label htmlFor="pc-pwd" className="text-right">{t("profile.oldPassword")}</Label>
                    <Input
                      id="pc-pwd"
                      type="password"
                      value={pwd}
                      onChange={(e) => setPwd(e.target.value)}
                      data-testid="pc-pwd"
                    />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[8em_1fr] sm:items-center">
                    <Label htmlFor="pc-npwd" className="text-right">{t("profile.newPassword")}</Label>
                    <Input
                      id="pc-npwd"
                      type="password"
                      value={npwd}
                      onChange={(e) => setNpwd(e.target.value)}
                      data-testid="pc-npwd"
                    />
                  </div>
                  <div className="grid gap-3 sm:grid-cols-[8em_1fr] sm:items-center">
                    <Label htmlFor="pc-npwd2" className="text-right">{t("profile.confirmPassword")}</Label>
                    <Input
                      id="pc-npwd2"
                      type="password"
                      value={npwd2}
                      onChange={(e) => setNpwd2(e.target.value)}
                      data-testid="pc-npwd2"
                    />
                  </div>
                  {pwdErr && (
                    <p className="text-sm text-destructive" data-testid="pc-pwd-err">
                      {pwdErr}
                    </p>
                  )}
                  <Button onClick={savePwd} data-testid="pc-save-pwd">
                    {t("profile.confirmChange")}
                  </Button>
                </div>
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
