import { useEffect, useState } from "react";
import { getStoredUser, SESSION_EVENT, type UserRow } from "./dataProvider";

export type Permission =
  | "rules.read"
  | "rules.test"
  | "rules.write"
  | "rules.activate"
  | "rules.delete"
  | "rules.generate"
  | "rules.explain_review"
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

export type Role = "admin" | "manager" | "auditor" | "viewer";

export const heldPermissions = (): Permission[] => {
  const cached = getStoredUser();
  const list = cached?.permissions;
  return Array.isArray(list) ? (list as Permission[]) : [];
};

const heldUser = (): UserRow | null => {
  const cached = getStoredUser();
  return cached && cached.id ? (cached as UserRow) : null;
};

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

export const can = (held: string[], permission: Permission): boolean =>
  held.includes(permission);

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

const permissionFor = (
  action: string,
  resource: string,
): Permission | undefined => RESOURCE_ACTIONS[resource]?.[action];

export const allows = (
  action: string,
  resource: string,
  held: string[] = heldPermissions(),
): boolean => {
  const permission = permissionFor(action, resource);
  if (!permission) return true;
  return can(held, permission);
};
