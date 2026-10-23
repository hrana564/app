import { SEVERITIES_ASC } from "../../theme";

// Ascending here, worst-first in the inbox: a "set the severity of a new rule"
// dropdown should start at the least alarming option, because that is the
// starting position, not the interesting one.
export const SEVERITY_OPTIONS = SEVERITIES_ASC;

// Mirrors the Literals in backend/app/schemas.py
export const STATUS_OPTIONS = ["draft", "active", "paused", "retired"] as const;


