import { createContext, useContext } from "react";

export type Me = { user_id: string; tenant_id: string; email: string };
export const SessionCtx = createContext<{ me: Me | null; logout: () => void }>({ me: null, logout: () => {} });
export const useSession = () => useContext(SessionCtx);
