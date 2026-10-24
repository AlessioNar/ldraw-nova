import { buildsApi } from "./api";

/** Ask, then delete a build with all its versions; its chats are kept (they move to Older chats). */
export async function confirmDeleteBuild(b: { id: string; title: string; versions: number }): Promise<boolean> {
  if (!confirm(`Delete the build "${b.title}"?

This removes its ${b.versions} version${b.versions === 1 ? "" : "s"}, the draft and its previews. Chats about it are kept. This cannot be undone.`)) return false;
  try { await buildsApi.remove(b.id); return true; } catch (e) { alert((e as Error).message); return false; }
}
