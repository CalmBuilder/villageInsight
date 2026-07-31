export type QuestionInputKey = {
  key: string;
  shiftKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  isComposing: boolean;
  keyCode: number;
};

export function questionInputKeyAction(
  event: QuestionInputKey,
): "submit" | "newline" | "native" {
  if (event.key !== "Enter") return "native";
  if (event.isComposing || event.keyCode === 229) return "native";
  if (event.shiftKey || event.ctrlKey) return "newline";
  if (event.metaKey || event.altKey) return "native";
  return "submit";
}
