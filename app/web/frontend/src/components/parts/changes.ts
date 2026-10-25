/** Every change to the collection reports here so the page can refresh and offer "Undo" for the source that changed. */
export type OnChanged = (text: string, undo?: { source: string }) => void;
