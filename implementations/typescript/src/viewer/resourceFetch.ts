export async function fetchJsonResource<T>(url: string, label: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (reason) {
    throw new Error(`${label} failed to load: ${reason instanceof Error ? reason.message : String(reason)}`, {cause: reason});
  }
  if (!response.ok) {
    const status = response.statusText.length === 0 ? String(response.status) : `${String(response.status)} ${response.statusText}`;
    throw new Error(`${label} failed to load (${status})`);
  }
  try {
    return await response.json() as T;
  } catch (reason) {
    throw new Error(`${label} is not valid JSON: ${reason instanceof Error ? reason.message : String(reason)}`, {cause: reason});
  }
}
