import { useState } from "react";
import { useLogin, useNotify } from "react-admin";
import { Alert, Box, Card, CardContent, Chip, Stack, TextField, Typography, Button, Divider, CircularProgress } from "@mui/material";
import LockOutlinedIcon from "@mui/icons-material/LockOutlined";

const DEMO = [
  { username: "admin_user", password: "Admin@2026", role: "admin", blurb: "authors rules, schedules and parameters" },
  { username: "audit_user", password: "Audit@2026", role: "auditor", blurb: "reviews runs and acknowledges tickets" },
];

export const Login = () => {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const login = useLogin();
  const notify = useNotify();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPending(true);
    try {
      await login({ username, password });
    } catch {
      notify("Invalid username or password", { type: "error" });
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
              Continuous auditing for Laduree &amp; Al Boom Marine
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