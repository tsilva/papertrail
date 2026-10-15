export async function readResponse<T>(response: Response, fallback: string): Promise<T> {
  // Servers and proxies can return plain-text errors instead of JSON.
  let data;
  try { data = await response.json(); }
  catch { throw new Error(fallback); }
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : fallback);
  return data as T;
}
