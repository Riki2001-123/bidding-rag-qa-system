import { PUBLIC_SHOWCASE } from "../deployment";
export const API_BASE = PUBLIC_SHOWCASE ? "" : (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api").replace(/\/$/, "");

export function getToken() {
  if (PUBLIC_SHOWCASE) return "";
  return localStorage.getItem("token") || "";
}

export function setToken(token) {
  if (PUBLIC_SHOWCASE) return;
  localStorage.setItem("token", token);
}

export async function apiFetch(path, options = {}) {
  if (PUBLIC_SHOWCASE) throw new Error("Live API is unavailable on the public showcase");
  const url = `${API_BASE}${path}`;
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  try {
    const response = await fetch(url, {
      ...options,
      headers
    });
    if (!response.ok) {
      const text = await response.text();
      const error = new Error(text || "Request failed");
      error.status = response.status;
      throw error;
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    return response.blob();
  } catch (err) {
    throw err;
  }
}
