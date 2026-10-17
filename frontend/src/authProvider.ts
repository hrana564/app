import type { AuthProvider } from "react-admin";
import {
  api,
  clearSession,
  getStoredUser,
  getToken,
  storeSession,
} from "./dataProvider";

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
    if (error?.status === 401 || error?.status === 403) {
      clearSession();
      throw new Error("Session expired");
    }
  },

  getIdentity: async () => {
    const cached = getStoredUser();
    if (!cached) throw new Error("Not authenticated");
    return { id: cached.id, fullName: cached.full_name || cached.username, role: cached.role };
  },

  getPermissions: async () => {
    const cached = getStoredUser();
    return cached?.role === "admin" ? "admin" : "auditor";
  },
};