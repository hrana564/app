import type { AuthProvider } from "react-admin";
import {
  api,
  clearSession,
  getStoredUser,
  getToken,
  storeSession,
} from "./dataProvider";
import { allows, heldPermissions } from "./permissions";

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

  getPermissions: async () => heldPermissions(),

  canAccess: async ({ action, resource }) =>
    allows(String(action), String(resource)),
};
