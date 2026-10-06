function hasUnpairedSurrogate(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return true;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function hasUnsafeUnicodeCategory(value: string): boolean {
  return /\p{C}/u.test(value);
}

function hasFormulaPrefix(value: string): boolean {
  return ["=", "+", "-", "@"].some((prefix) => value.trimStart().startsWith(prefix));
}

export type StaffingManagerLabelIssue = "blank" | "too_long" | "unsafe";

export function staffingManagerLabelIssue(value: string): StaffingManagerLabelIssue | null {
  if (value.trim() === "") return "blank";
  if (Array.from(value).length > 128) return "too_long";
  if (
    hasUnpairedSurrogate(value) ||
    hasUnsafeUnicodeCategory(value) ||
    hasFormulaPrefix(value)
  ) {
    return "unsafe";
  }
  return null;
}

/** Mirrors the closed staffing-domain operator contract before a request ID is allocated. */
export function requireStaffingManagerLabel(value: string): string {
  const issue = staffingManagerLabelIssue(value);
  if (issue === "blank") throw new Error("A nonblank manager label is required.");
  if (issue === "too_long") {
    throw new Error("The manager label must not exceed 128 Unicode characters.");
  }
  if (issue === "unsafe") throw new Error("The manager label contains unsafe characters.");
  return value;
}
