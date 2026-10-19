import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/**
 * The client entity every screen is scoped to.
 *
 * Scope Investments audits two unrelated clients. Their data lives in separate
 * folders, their rules are separate rules and their tickets are separate
 * tickets - but they share this portal, so the choice of which client you are
 * looking at belongs to the shell, not to each screen. A filter repeated on five
 * toolbars is five chances to disagree with the one above it.
 *
 * The codes and names are client identity, and the backend owns that list in
 * `app/engine/datasources.py`. It is repeated here because the app bar has to
 * render before anything has loaded; nothing derives behaviour from it beyond
 * sending the code back.
 */
export const ENTITIES = [
  { code: "ABM", name: "Al Boom Marine" },
  { code: "LAD", name: "Laduree" },
] as const;

export const DEFAULT_ENTITY = "LAD";

const STORAGE_KEY = "abm.entity";

const isKnown = (code: unknown): code is string =>
  ENTITIES.some((e) => e.code === code);

const EntityContext = createContext<{ entity: string; setEntity: (code: string) => void }>({
  entity: DEFAULT_ENTITY,
  setEntity: () => undefined,
});

export const EntityScope = ({ children }: { children: ReactNode }) => {
  // Read once, at mount. A stored code that no longer exists falls back to the
  // default rather than scoping the whole app to nothing.
  const [entity, set] = useState<string>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return isKnown(saved) ? saved : DEFAULT_ENTITY;
    } catch {
      return DEFAULT_ENTITY;
    }
  });

  const value = useMemo(
    () => ({
      entity,
      setEntity: (code: string) => {
        if (!isKnown(code)) return;
        try {
          localStorage.setItem(STORAGE_KEY, code);
        } catch {
          /* private browsing: the choice just will not survive a reload */
        }
        set(code);
      },
    }),
    [entity],
  );

  return <EntityContext.Provider value={value}>{children}</EntityContext.Provider>;
};

export const useEntityScope = () => useContext(EntityContext);