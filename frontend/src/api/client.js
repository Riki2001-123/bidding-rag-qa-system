const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000/api";

export function getToken() {
  return localStorage.getItem("token") || "";
}

export function setToken(token) {
  localStorage.setItem("token", token);
}

export async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  console.log(`[apiFetch] >>> ${options.method || "GET"} ${url}`);
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
    console.log(`[apiFetch] <<< ${response.status} ${url}`);
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || "请求失败");
    }
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }
    return response.blob();
  } catch (err) {
    console.log(`[apiFetch] ERR ${url}: ${err.message}`);
    throw err;
  }
}
