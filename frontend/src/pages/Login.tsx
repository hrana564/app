import { useEffect, useState } from "react";
import { Alert, Box, Card, CardContent, Chip, Collapse, Stack, TextField, Typography, Button, Divider, CircularProgress } from "@mui/material";
import ShieldOutlinedIcon from "@mui/icons-material/ShieldOutlined";
import InfoOutlinedIcon from "@mui/icons-material/InfoOutlined";
import { api, storeSession, takeReturnTo, type DemoAccount } from "../dataProvider";

export const Login = () => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [demo, setDemo] = useState<DemoAccount[] | null>(null);
  const [demoOpen, setDemoOpen] = useState(false);

  useEffect(() => {
    let live = true;
    api
      .demoAccounts()
      .then((list) => { if (live) setDemo(list); })
      .catch(() => { if (live) setDemo([]); });
    return () => { live = false; };
  }, []);

  const enterAs = async (u: string, p: string) => {
    if (pending) return;
    setUsername(u);
    setPassword(p);
    setPending(true);
    setError(null);
    try {
      const result = await api.login(u, p);
      const destination = takeReturnTo() ?? "/";
      window.location.hash = `#${destination}`;
      storeSession(result.access_token, result.user);
    } catch (err: any) {
      setError(err?.message || "Invalid username or password");
    } finally {
      setPending(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const result = await api.login(username, password);
      // The deep link the visitor was actually sent is where they belong
      // afterwards, not the dashboard. The address is set BEFORE the session
      // is stored, because the router is created when the admin mounts and
      // reads the address then. The dashboard is the fallback, and it is also
      // what takes the visitor off the sign-in route - which react-admin
      // would otherwise answer with a sign-in form.
      const destination = takeReturnTo() ?? "/";
      window.location.hash = `#${destination}`;
      storeSession(result.access_token, result.user);
    } catch (err: any) {
      setError(err?.message || "Invalid username or password");
    } finally {
      setPending(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 2,
        background:
          "radial-gradient(1200px 600px at 85% -10%, rgba(15,138,128,0.35), transparent 60%), radial-gradient(1000px 700px at -10% 110%, rgba(43,107,168,0.35), transparent 55%), linear-gradient(135deg, #0a2540 0%, #123a63 55%, #0e3a3a 130%)",
        p: 2,
      }}
    >
      <Card sx={{ width: 480, maxWidth: "100%", borderRadius: 4, boxShadow: "0 18px 50px rgba(3,12,24,0.45)" }}>
        <CardContent sx={{ p: { xs: 3, sm: 4 } }}>
          <Stack spacing={1.25} sx={{ mb: 3, alignItems: "flex-start" }}>
            <Stack direction="row" spacing={1.5} sx={{ alignItems: "center", width: "100%" }}>
              <Box
                sx={{
                  width: 52, height: 52, borderRadius: 3.5, flexShrink: 0,
                  background: "linear-gradient(135deg, #123a63 0%, #0f8a80 130%)",
                  color: "#fff", display: "flex", alignItems: "center", justifyContent: "center",
                  boxShadow: "0 6px 18px rgba(10,37,64,0.35)",
                }}
              >
                <ShieldOutlinedIcon fontSize="large" />
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <Typography
                  variant="overline"
                  sx={{ color: "primary.main", letterSpacing: 1.6, lineHeight: 1 }}
                >
                  SquareOne Holdings
                </Typography>
                <Typography variant="h5" sx={{ lineHeight: 1.15, mt: 0.5 }}>
                  Agentic Data Analytics
                </Typography>
              </Box>
            </Stack>
          </Stack>

          <form onSubmit={submit}>
            <Stack spacing={1.75}>
              <TextField
                label="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                fullWidth
                required
                size="medium"
              />
              <TextField
                label="Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                fullWidth
                required
                size="medium"
              />
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={pending}
                startIcon={pending ? <CircularProgress size={18} color="inherit" /> : undefined}
                sx={{ py: 1.25, fontWeight: 700, borderRadius: 2.5 }}
              >
                {pending ? "Signing in…" : "Sign in to assurance"}
              </Button>
            </Stack>
          </form>

          {error && (
            <Alert severity="error" sx={{ mt: 2 }}>
              {error}
            </Alert>
          )}

          {demo && demo.length > 0 && (
            <>
              <Divider sx={{ my: 3 }}>
                <Typography
                  variant="caption"
                  component="button"
                  onClick={() => setDemoOpen((v) => !v)}
                  sx={{
                    display: "inline-flex", alignItems: "center", gap: 0.5,
                    color: "text.secondary", cursor: "pointer",
                    bgcolor: "background.paper", border: 0, p: 0,
                    font: "inherit", letterSpacing: "inherit",
                    "&:hover": { color: "text.primary" },
                  }}
                >
                  {demoOpen ? "HIDE DEMO ACCOUNTS" : "TRY A ROLE — ONE CLICK TO ENTER"}
                </Typography>
              </Divider>

              <Collapse in={demoOpen} unmountOnExit>
                <Stack spacing={0.75}>
                  {demo.map((d) => (
                    <Box
                      key={d.username}
                      onClick={() => void enterAs(d.username, d.password)}
                      sx={{
                        p: 1.25, border: "1px solid #e3e8f0", borderRadius: 2,
                        cursor: pending ? "wait" : "pointer",
                        opacity: pending ? 0.6 : 1,
                        "&:hover": { borderColor: "primary.main", bgcolor: "#f7faff" },
                      }}
                    >
                      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
                        <Chip
                          size="small"
                          label={d.role}
                          color={
                            d.role === "admin" ? "primary"
                              : d.role === "manager" ? "secondary"
                                : "default"
                          }
                        />
                        <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                          {d.username}
                        </Typography>
                        <Typography
                          variant="caption"
                          sx={{ ml: "auto !important", fontFamily: "monospace", color: "text.secondary", letterSpacing: 2 }}
                        >
                          ••••••
                        </Typography>
                      </Stack>
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>
                        {d.blurb} — click to sign in
                      </Typography>
                    </Box>
                  ))}
                  <Stack direction="row" spacing={0.75} sx={{ pt: 1, alignItems: "flex-start", color: "text.secondary" }}>
                    <InfoOutlinedIcon sx={{ fontSize: 16, mt: 0.2, flexShrink: 0 }} />
                    <Typography variant="caption">
                      Demo build — accounts are seeded from <code>backend/.env</code> once, passwords stay bcrypt-hashed in PostgreSQL.
                    </Typography>
                  </Stack>
                </Stack>
              </Collapse>
            </>
          )}

        </CardContent>
      </Card>
      <Typography variant="caption" sx={{ color: "rgba(255,255,255,0.72)", textAlign: "center" }}>
        © SquareOne Holdings
      </Typography>
    </Box>
  );
};
