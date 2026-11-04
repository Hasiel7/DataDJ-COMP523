// Accounts: register, sign in and out, and authenticated requests to the API.
// The session (token + user) lives in localStorage and is cleared when the API
// answers 401 to a request that carried it.

export interface User {
    id: string;
    email: string;
    created_at: string;
}

interface AuthResponse {
    access_token: string;
    token_type: 'bearer';
    user: User;
}

// Same API as the Reduction Lab (reduction-api.ts)
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000/api';
const TOKEN_KEY = 'datadj.auth.token';
const USER_KEY = 'datadj.auth.user';

// The API answered with an error status
export class ApiError extends Error {
    readonly status: number;

    constructor(message: string, status: number) {
        super(message);
        this.status = status;
    }
}

// The API couldn't be reached at all (server down, offline, CORS)
export class NetworkError extends Error { }

const listeners = new Set<(user: User | null) => void>();

function readUser(): User | null {
    try {
        const user = JSON.parse(localStorage.getItem(USER_KEY) ?? 'null');
        return user && typeof user.id === 'string' && typeof user.email === 'string' ? user : null;
    } catch {
        return null;
    }
}

let currentUser: User | null = localStorage.getItem(TOKEN_KEY) ? readUser() : null;

export function getUser(): User | null {
    return currentUser;
}

// Called with the new user (or null) whenever someone signs in or out
export function onAuthChange(listener: (user: User | null) => void) {
    listeners.add(listener);
}

function setSession(token: string | null, user: User | null) {
    if (token && user) {
        localStorage.setItem(TOKEN_KEY, token);
        localStorage.setItem(USER_KEY, JSON.stringify(user));
    } else {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
    }
    const changed = currentUser?.id !== user?.id;
    currentUser = user;
    if (changed) listeners.forEach((listener) => listener(user));
}

// fetch against the API, with the session's token when there is one
export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
    const token = localStorage.getItem(TOKEN_KEY);
    const headers = new Headers(init.headers);
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');

    let response: Response;
    try {
        response = await fetch(`${API_BASE}${path}`, { ...init, headers });
    } catch {
        throw new NetworkError("Can't reach the Data DJ server.");
    }
    if (response.status === 401 && token) setSession(null, null); // expired or revoked

    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
        const detail = payload && typeof payload === 'object' && 'detail' in payload
            ? (payload as { detail: unknown }).detail
            : `Request failed (${response.status})`;
        throw new ApiError(errorText(detail), response.status);
    }
    return payload as T;
}

// FastAPI validation errors are a list of {msg, loc}; show the first readably
function errorText(detail: unknown): string {
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail[0]?.msg) {
        const field = detail[0].loc?.at(-1);
        const msg = String(detail[0].msg).replace(/^Value error, /, '');
        return field === 'password' && /at least/.test(msg) ? 'Password must be at least 8 characters.' : msg;
    }
    return JSON.stringify(detail);
}

async function authenticate(path: string, email: string, password: string): Promise<User> {
    const body = await apiRequest<AuthResponse>(path, {
        method: 'POST',
        body: JSON.stringify({ email, password }),
    });
    setSession(body.access_token, body.user);
    return body.user;
}

export function register(email: string, password: string): Promise<User> {
    return authenticate('/auth/register', email, password);
}

export function login(email: string, password: string): Promise<User> {
    return authenticate('/auth/login', email, password);
}

export function logout() {
    setSession(null, null);
}

// Check a remembered session at startup. A 401 signs out (in apiRequest);
// an unreachable server keeps the remembered user so the page still works.
export async function refreshUser(): Promise<void> {
    if (!localStorage.getItem(TOKEN_KEY)) return;
    try {
        const user = await apiRequest<User>('/auth/me');
        const token = localStorage.getItem(TOKEN_KEY);
        if (token) setSession(token, user);
    } catch {
        // NetworkError: stay signed in locally; ApiError 401: already signed out
    }
}
