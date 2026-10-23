import { useNavigate, useParams } from "react-router";
import { ShowBase, useShowContext } from "react-admin";
import { Box, Button, Typography } from "@mui/material";
import { ruleShowPath, versionFromParam } from "../../rulePaths";
import { RuleDetails } from "./RuleDetails";

const RuleVersionMissing = () => {
  const { id } = useParams();
  const { error } = useShowContext<any>();
  const navigate = useNavigate();
  const detail =
    // The server's refusal is more useful than "something went wrong": it says
    // which versions do exist.
    (error as any)?.body?.detail ??
    error?.message ??
    "This rule has no such version.";
  return (
    <Box sx={{ p: 3, maxWidth: 640 }}>
      <Typography variant="h6" sx={{ mb: 1 }}>That version does not exist</Typography>
      <Typography variant="body2" sx={{ color: "text.secondary", mb: 2 }}>
        {detail}
      </Typography>
      <Button variant="contained" onClick={() => navigate(ruleShowPath(id ?? ""))}>
        Open the current version
      </Button>
    </Box>
  );
};

export const RuleShow = () => {
  const { version } = useParams();
  const asked = versionFromParam(version);
  return (
    // `resource` because this page is reached by its own route, outside the
    // resource's generated ones - without it the show controller has no idea
    // which resource it is showing. `meta` is how the route segment reaches the
    // request: the provider turns it into `?version=n`, so the record on the
    // page IS the version in the URL.
    <ShowBase
      resource="rules"
      queryOptions={{ meta: asked ? { version: asked } : undefined }}
      error={<RuleVersionMissing />}
    >
      <RuleDetails asked={asked} />
    </ShowBase>
  );
};
