import { useState } from "react";
import { Alert, Box, Card, CardContent, Chip, Stack, TextField, Typography, Button, Divider, CircularProgress } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";
import { api, storeSession, takeReturnTo } from "../dataProvider";

const DEMO = [
  { username: "admin_user", password: "Admin@2026", role: "admin", blurb: "authors rules, schedules and parameters" },
  { username: "audit_user", password: "Audit@2026", role: "auditor", blurb: "reviews runs and acknowledges tickets" },
];

/**
 * The sign-in form.
 *
 * It stands on its own: with no session there is no react-admin admin around
 * it, so it cannot use react-admin's `useLogin` or `useNotify`. It signs in
 * through the same data provider everything else uses, and reports a refusal
 * in the page rather than in a snackbar - a snackbar needs the admin too.
 */
export const Login = () => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
          <Stack alignItems="center" spacing={1} sx={{ mb: 3 }}>
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
            <Typography variant="body2" color="text.secondary" textAlign="center">
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

          <Divider sx={{ my: 3 }}>
            <Typography variant="caption" color="text.secondary">
              DEMO ACCOUNTS
            </Typography>
          </Divider>

          <Stack spacing={1}>
            {DEMO.map((d) => (
              <Box
                key={d.username}
                onClick={() => {
                  setUsername(d.username);
                  setPassword(d.password);
                }}
                sx={{
                  p: 1.5, border: "1px solid #e3e8f0", borderRadius: 2,
                  cursor: "pointer", "&:hover": { borderColor: "primary.main", bgcolor: "#f7faff" },
                }}
              >
                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip size="small" label={d.role} color={d.role === "admin" ? "primary" : "secondary"} />
                  <Typography variant="body2" sx={{ fontFamily: "monospace", fontWeight: 700 }}>
                    {d.username}
                  </Typography>
                </Stack>
                <Typography variant="caption" color="text.secondary">
                  {d.blurb}
                </Typography>
              </Box>
            ))}
          </Stack>

          <Alert severity="info" sx={{ mt: 2 }}>
            Passwords are stored bcrypt-hashed in PostgreSQL. The values in{" "}
            <code>backend/.env</code> are only used to seed the two accounts once.
          </Alert>
        </CardContent>
      </Card>
    </Box>
  );
};