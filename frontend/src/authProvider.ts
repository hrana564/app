import type { AuthProvider } from "react-admin";
import {
  api,
  clearSession,
  getStoredUser,
  getToken,
  storeSession,
} from "./dataProvider";
import { allows, heldPermissions } from "./permissions";

/**
 * A refusal is not an expiry.
 *
 * `checkError` used to treat a 403 exactly like a 401: clear the session and
 * report "Session expired". Under a capability model that is wrong in a way
 * that would be felt constantly - an auditor who pressed a button they were
 * not allowed to press got signed out of the portal for it.
 *
 * react-admin signs the user out whenever `checkError` throws, unless the
 * thrown error says otherwise. `logoutUser: false` is how it is told to notify
 * without signing anybody out, and the message it shows is the one we set.
 */
const refusal = (message: string) => {
  const error = new Error(message) as Error & { logoutUser?: boolean };
  error.logoutUser = false;
  return error;
};

export const authProvider: AuthProvider = {
  login: async ({ username, password }) => {
    const result = await api.login(username, password);
    storeSession(result.access_token, result.user);
  },

  logout: async () => {
    clearSession();
  },

  checkAuth: async () => {
    if (!getToken()) throw new Error("Not authenticated");
  },

  checkError: async (error: { status?: number }) => {
    if (error?.status === 401) {
      clearSession();
      const expired = new Error("Session expired") as Error & { logoutUser?: boolean };
      expired.logoutUser = true;
      throw expired;
    }
    if (error?.status === 403) {
      throw refusal("You do not have permission to do that.");
    }
  },

  getIdentity: async () => {
    const cached = getStoredUser();
    if (!cached) throw new Error("Not authenticated");
    return { id: cached.id, fullName: cached.full_name || cached.username, role: cached.role };
  },

  /**
   * The capabilities of the signed-in user.
   *
   * A list rather than a role string, because the list is what the screens
   * ask about. A role is for display; a capability is for a decision.
   */
  getPermissions: async () => heldPermissions(),

  /**
   * May this user take this action on this resource?
   *
   * react-admin's `useCanAccess` and `useRequireAccess` both ask here, so the
   * route guard and the menu item that leads to it come from one answer.
   */
  canAccess: async ({ action, resource }) =>
    allows(String(action), String(resource)),
};
