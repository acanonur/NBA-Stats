/**
 * A `subject` config field (`stat_tile.subjectId`, `shot_profile.subjectId`): a player id, a
 * team id, or a `$favorite_player`-style token, depending on the sibling field named by
 * `spec.dependsOn` (almost always `subjectType`, `"player" | "team"`).
 */
import type { JSX } from "react";
import { PlayerField } from "./PlayerField";
import { TeamField } from "./TeamField";
import type { FieldEditorProps } from "./types";

export function SubjectField({
  spec,
  value,
  onChange,
  config,
}: FieldEditorProps<number | string | null>): JSX.Element {
  const subjectType = spec.dependsOn ? config[spec.dependsOn] : undefined;
  if (subjectType === "team") {
    return <TeamField spec={spec} value={value} onChange={onChange} config={config} />;
  }
  return <PlayerField spec={spec} value={value} onChange={onChange} config={config} />;
}
