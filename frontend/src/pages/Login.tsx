import { useEffect, useState } from "react";
import { Alert, Box, Card, CardContent, Chip, Collapse, Stack, TextField, Typography, Button, Divider, CircularProgress } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { api, storeSession, takeReturnTo, type DemoAccount } from "../dataProvider";

/**
 * The sign-in form.
 *
 * It stands on its own: with no session there is no react-admin admin around
 * it, so it cannot use react-admin's `useLogin` or `useNotify`. It signs in
 * through the same data provider everything else uses, and reports a refusal
 * in the page rather than in a snackbar - a snackbar needs the admin too.
 *
 * THE DEMO PANEL
 * --------------
 * One account per role, offered with its password so a reviewer can try each
 * one without being given accounts by hand. The list comes from the API
 * rather than living here, because the panel and the seed would otherwise be
 * two copies of the same list - and two copies of a list of passwords drift
 * the first time somebody changes one.
 *
 * The API answers with nothing unless the deployment has opted in, so the
 * panel simply is not there for a deployment that does not want its
 * passwords advertised.
 */
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
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg, #0a2540 0%, #123a63 55%, #0f8a80 150%)",
        p: 2,
      }}
    >
      <Card sx={{ width: 440, maxWidth: "100%" }}>
        <CardContent sx={{ p: 4 }}>
          <Stack spacing={1} sx={{ mb: 3, alignItems: "center" }}>
            <Box
              sx={{
                width: 52, height: 52, borderRadius: "50%",
                bgcolor: "primary.main", color: "#fff",
                display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              <LockOutlinedIcon fontSize="large" />
            </Box>
            <Typography variant="h5">Scope Investments — Audit Portal</Typography>
            <Typography variant="body2" sx={{ color: "text.secondary", textAlign: "center" }}>
              Continuous auditing
            </Typography>
          </Stack>

          <form onSubmit={submit}>
            <Stack spacing={2}>
              <TextField
                label="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
                autoComplete="username"
                fullWidth
                required
              />
              <TextField
                label="Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                fullWidth
                required
              />
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={pending}
                startIcon={pending ? <CircularProgress size={18} color="inherit" /> : undefined}
              >
                Sign in
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
                  DEMO ACCOUNTS
                </Typography>
              </Divider>

              <Collapse in={demoOpen} unmountOnExit>
                <Stack spacing={0.75}>
                  {demo.map((d) => (
                    <Box
                      key={d.username}
                      onClick={() => {
                        setUsername(d.username);
                        setPassword(d.password);
                      }}
                      sx={{
                        p: 1.25, border: "1px solid #e3e8f0", borderRadius: 2,
                        cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "#f7faff" },
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
                          sx={{ ml: "auto !important", fontFamily: "monospace", color: "text.secondary" }}
                        >
                          {d.password}
                        </Typography>
                      </Stack>
                      <Typography variant="caption" sx={{ color: "text.secondary" }}>
                        {d.blurb}
                      </Typography>
                    </Box>
                  ))}
                </Stack>
              </Collapse>
            </>
          )}

          <Alert severity="info" sx={{ mt: 2 }}>
            Passwords are bcrypt-hashed in PostgreSQL. The values in{" "}
            <code>backend/.env</code> only seed the accounts once.
          </Alert>
        </CardContent>
      </Card>
    </Box>
  );
};
