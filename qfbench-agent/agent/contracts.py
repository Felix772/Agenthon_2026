"""Conservative output requirements extracted only from the supplied instruction.

This is an engineering check, not a financial verifier. Requirements that cannot
be mapped unambiguously to an artifact remain visible as unchecked statements.
"""
import re

from .workspace import deliverable_name

FILE = re.compile(r"(?<![\w/])(?:/app/output/|/output/)?[\w.-]+(?:/[\w.-]+)*\.(?:json|csv|tsv|parquet|xlsx|txt|md|png|pdf|py)\b", re.I)
WRITE = re.compile(r"\b(?:write|save|create|produce|export|generate|output)\b", re.I)
OUTPUT_HEADING = re.compile(r"^\s*#{1,6}\s+(?:(?:step\s+\d+\s*:\s*)?(?:save|write|create)\s+)?(?:required\s+)?(?:outputs?|deliverables?|submission)(?:\s|:|$)", re.I)
NON_REQUIRED = re.compile(r"\b(?:optional|example|may|if desired|do not|don't|must not|should not|not required)\b|\be\.g\.", re.I)


def _local_qualifiers(before, after):
    """Keep qualifiers in the filename's clause, excluding other sentences."""
    before = re.sub(r"\be\.g\.", "example", before, flags=re.I)
    boundaries = list(re.finditer(r"[.;!?]\s+", before))
    if boundaries:
        before = before[boundaries[-1].end():]
    after = re.split(r"[.;!?](?:\s|$)", after, maxsplit=1)[0]
    next_file = FILE.search(after)
    if next_file:
        # Qualifiers introduced with a conjunction/comma before another file
        # belong to that later file, not this one.
        prefix = after[:next_file.start()]
        later_qualifier = re.search(
            r"(?:,\s*|\b(?:and|while|but)\s+)(?:optional|for example|e\.g\.|do not|don't|not required|must not|should not)(?=\W|$)",
            prefix, re.I)
        after = prefix[:later_qualifier.start()] if later_qualifier else prefix
    return before + " " + after


def _declares_output(before, after, output_section):
    verbs = list(WRITE.finditer(before))
    if verbs:
        tail = before[verbs[-1].end():]
        if len(tail) <= 100 and not re.search(r"\b(?:using|from|based on|read|load|input|configured in|according to)\b|[;!?]|\.(?:\s|$)", tail, re.I):
            return True
    # Output section membership alone is insufficient: prose commonly refers
    # back to inputs such as params.json. Accept an actual heading/list label.
    label = re.fullmatch(r"\s*(?:#{1,6}\s*)?(?:(?:\d+[.)]|[-*+])\s*)?(?:(?:file|artifact)\s+\d+\s*:\s*)?(?:\*{1,2})?[`\"']?", before, re.I)
    rest = after.lstrip("`\"'* ")
    return bool(output_section and label and (not rest or rest.startswith((":", "—", "–", "-", "("))))


def instruction_contract(instruction):
    requirements = {}
    unchecked = []
    output_heading_depth = None
    for number, line in enumerate(instruction.splitlines(), 1):
        heading = re.match(r"^\s*(#{1,6})\s", line)
        if heading:
            depth = len(heading.group(1))
            if OUTPUT_HEADING.match(line) and not NON_REQUIRED.search(line):
                output_heading_depth = depth
            elif output_heading_depth is not None and depth <= output_heading_depth:
                output_heading_depth = None
        if re.search(r"\b(?:units?|precision|round(?:ing|ed)?|decimal|order|finite)\b", line, re.I):
            unchecked.append({"line": number, "text": line[:1000]})
        for match in FILE.finditer(line):
            before = line[:match.start()]
            # A nearby production verb or explicit output path is positive
            # evidence. Input/read references and optional examples are not.
            explicit_path = match.group().startswith(("/output/", "/app/output/"))
            after = line[match.end():]
            required = explicit_path or _declares_output(before, after, output_heading_depth is not None)
            if not required or NON_REQUIRED.search(_local_qualifiers(before, after)):
                continue
            if re.search(r"\b(?:read|load|input|from)\s+(?:file\s+)?[`\"']?\s*$", before, re.I):
                continue
            name = match.group().removeprefix("/app/output/").removeprefix("/output/")
            try:
                deliverable_name(name)
            except ValueError:
                continue
            item = requirements.setdefault(name, {"path": name, "source_lines": [], "columns": [], "keys": []})
            item["source_lines"].append(number)
            # Bind schema to a single file on the line. Anything more complex
            # is left in the original instruction for the runtime solver.
            if len(FILE.findall(line)) != 1:
                continue
            after = line[match.end():]
            # Only a contiguous explicit list after plural columns/keys is a
            # schema. "no index column, sorted by `date`" is not one.
            schema = re.search(r"\b(columns|top-level keys)\s*(?:(?:must be|are|named|of|exactly|only)\s*)?[:=]?\s*((?:`[A-Za-z_][\w.-]*`\s*(?:,|and)?\s*)+)", after, re.I)
            if schema:
                names = re.findall(r"`([A-Za-z_][\w.-]*)`", schema.group(2))
                if names:
                    field = "columns" if schema.group(1).lower().startswith("column") else "keys"
                    item[field] = list(dict.fromkeys(names))
                    rest = after[schema.end():].strip()
                    complete_list = not rest or rest.startswith((".", ";", ":", "(", ")")) or bool(re.match(r"in (?:this|the specified|exact) order\b", rest, re.I))
                    if field == "columns":
                        item["column_order"] = bool(re.search(r"\bin (?:this|the specified|exact) order\b", after, re.I))
                        item["exact_columns"] = complete_list and bool(re.search(r"\b(?:columns\s+(?:exactly|only)|(?:exactly|only)\s+(?:these\s+)?columns|no extra columns)\b", after, re.I))
                    else:
                        item["exact_keys"] = complete_list and bool(re.search(r"\b(?:keys\s+(?:exactly|only)|(?:exactly|only)\s+(?:these\s+)?(?:top-level )?keys|no extra (?:top-level )?keys)\b", after, re.I))
            item["finite"] = item.get("finite", False) or bool(re.search(r"\b(?:all (?:numeric )?values|numeric values)\s+(?:must be|are)\s+finite\b", after, re.I))
    return {"version": 1, "required_artifacts": list(requirements.values()),
            "unchecked_statements": unchecked[:100],
            "coverage": "Conservative explicit filenames and same-line schemas only; numerical correctness, units, row order and precision require program self-checks and the external verifier."}
