"use client";

import { createContext, useContext } from "react";

/** Which consoles this account may open — checked once at sign-in. */
export type Access = { admin: boolean; marketing: boolean };
export type Me = { user_id: string; tenant_id: string; email: string; access: Access };
export const SessionCtx = createContext<{ me: Me | null; logout: () => void }>({ me: null, logout: () => {} });
export const useSession = () => useContext(SessionCtx);
