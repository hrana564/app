/**
 * What the signed-in user may do.
 *
 * The list arrives from the server with the session. It is resolved from the
 * capability matrix in `backend/app/permissions.py` and travels in the login
 * response and in `GET /api/auth/me`. The frontend never re-derives it from a
 * role string: a second derivation is a second answer to "may I?", and the
 * two drift the first time somebody adds a role - and they drift silently,
 * because the screen still looks right.
 *
 * The vocabulary below is a TYPE, not a policy. The server decides what a role
 * holds; this file only names the capabilities, so that a check against a
 * misspelled name is a compile error rather than a check that is silently
 * always false.
 *
 * Nothing here is the gate. The server refuses an action it does not permit,
 * and these checks are what stop the screen offering a button whose answer is
 * already known to be no.
 */
import { useEffect, useState } from "react";
import { getStoredUser, SESSION_EVENT, type UserRow } from "./dataProvider";

export type Permission =
  | "rules.read"
  | "rules.test"
  | "rules.write"
  | "rules.activate"
  | "rules.delete"
  | "rules.generate"
  | "schedules.read"
  | "schedules.write"
  | "runs.read"
  | "runs.rerun"
  | "tickets.read"
  | "tickets.acknowledge"
  | "datasources.read"
  | "datasources.write"
  | "assurance.read"
  | "users.read"
  | "users.write";

/**
 * The roles, as a vocabulary.
 *
 * A type, not a policy: the server decides which roles exist and what they
 * hold, and the screens ask it. This names them so that a misspelling is a
 * compile error rather than a check that is silently always false.
 */
export type Role = "admin" | "manager" | "auditor" | "viewer";

/** The full vocabulary, in the order the server sends it. */
export const PERMISSIONS: Permission[] = [
  "rules.read",
  "rules.test",
  "rules.write",
  "rules.activate",
  "rules.delete",
  "rules.generate",
  "schedules.read",
  "schedules.write",
  "runs.read",
  "runs.rerun",
  "tickets.read",
  "tickets.acknowledge",
  "datasources.read",
  "datasources.write",
  "assurance.read",
  "users.read",
  "users.write",
];

/**
 * The capabilities the signed-in user holds.
 *
 * Read from the cached session rather than fetched, so a screen can ask the
 * question while it renders instead of after a round trip. The cache is
 * refreshed from `GET /api/auth/me` when the app starts, so a role changed by
 * an administrator is picked up on the next page load rather than at the next
 * sign-in.
 */
export const heldPermissions = (): Permission[] => {
  const cached = getStoredUser();
  const list = cached?.permissions;
  return Array.isArray(list) ? (list as Permission[]) : [];
};

/** The cached account, or null when nobody is signed in. */
export const heldUser = (): UserRow | null => {
  const cached = getStoredUser();
  return cached && cached.id ? (cached as UserRow) : null;
};

/**
 * The signed-in account, kept in step with the session.
 *
 * Read from the cache rather than fetched, because the app bar wants to say
 * who is signed in on the first frame - and the cache is refreshed from
 * `GET /api/auth/me` when the app starts, so it is not the answer from
 * whenever the sign-in happened.
 */
export const useCurrentUser = (): UserRow | null => {
  const [user, setUser] = useState<UserRow | null>(heldUser);

  useEffect(() => {
    const onSession = () => setUser(heldUser());
    window.addEventListener(SESSION_EVENT, onSession);
    onSession();
    return () => window.removeEventListener(SESSION_EVENT, onSession);
  }, []);

  return user;
};

/** Does this set of capabilities include the one asked for? */
export const can = (held: string[], permission: Permission): boolean =>
  held.includes(permission);

/**
 * The capabilities of the signed-in user, kept in step with the session.
 *
 * A role change lands by a new sign-in, a sign-out, or the refresh at
 * start-up - each of which fires `SESSION_EVENT`. Without the subscription the
 * list would be right at sign-in and wrong for the rest of the afternoon.
 */
export const usePermissions = (): Permission[] => {
  const [held, setHeld] = useState<Permission[]>(heldPermissions);

  useEffect(() => {
    const onSession = () => setHeld(heldPermissions());
    window.addEventListener(SESSION_EVENT, onSession);
    onSession();
    return () => window.removeEventListener(SESSION_EVENT, onSession);
  }, []);

  return held;
};

/**
 * Which capability guards a react-admin action on a resource.
 *
 * The action names are react-admin's own - list, show, create, edit, delete -
 * and this maps them onto the vocabulary the server uses. A resource or an
 * action that is not listed is ALLOWED: the server is the gate, and a screen
 * that hid an unmapped resource would lock everybody out of it rather than
 * keeping them out of something they may do.
 *
 * The accounts are NOT here. They are not a react-admin resource - they live
 * under Settings, as a tab - so the capability that guards them is asked
 * directly with `can(perms, "users.read")`. A mapping for a resource that
 * does not exist is a mapping that nobody keeps true.
 */
const RESOURCE_ACTIONS: Record<string, Partial<Record<string, Permission>>> = {
  rules: {
    list: "rules.read",
    show: "rules.read",
    create: "rules.write",
    edit: "rules.write",
    delete: "rules.delete",
  },
  runs: { list: "runs.read", show: "runs.read" },
  tickets: { list: "tickets.read", show: "tickets.read" },
  datasources: { list: "datasources.read" },
};

/** The capability that guards one action on one resource, if any does. */
export const permissionFor = (
  action: string,
  resource: string,
): Permission | undefined => RESOURCE_ACTIONS[resource]?.[action];

/**
 * May the signed-in user take this action on this resource?
 *
 * Used by react-admin's `canAccess`, and by the menu - one answer, so the
 * menu and the screen it opens cannot disagree about what is permitted.
 */
export const allows = (
  action: string,
  resource: string,
  held: string[] = heldPermissions(),
): boolean => {
  const permission = permissionFor(action, resource);
  if (!permission) return true;
  return can(held, permission);
};
