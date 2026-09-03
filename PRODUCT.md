# Product

The current platform uses SQLite schema version 3 as its internal source of truth, with CSV and JSONL as explicit export formats. See `docs/architecture.md` for data lineage and security boundaries.

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated by the user: one Python 3 standard-library program serving a local browser interface.

## Users

Dataset annotators and quality reviewers working through local video folders on Windows. Their job is to verify automatic behavior prelabels, mark person type, capture event time ranges, and record an explicit review outcome.

## Product Purpose

The tool turns a video folder into a resumable CSV annotation workflow. It removes manual manifest preparation while preserving human judgement for behavior labels and event timing.

## Positioning

Unlike a generic video player, this is a local, CSV-first reviewer: it combines directory-derived prelabels, multi-event timing, explicit audit state, and safe in-place CSV updates without deployment or third-party setup.

## Operating Context

The application runs from `D:\default file\视频标注工具` on a Windows workstation and opens at `127.0.0.1`. It reads local videos and CSV files only. Typical datasets use `pos`/`neg` folders and filenames such as `dog_out`, `fall`, and `peep_car`.

## Capabilities and Constraints

- Imports video folders recursively and incrementally maintains `video_labeler_manifest.csv`.
- Supports the requested fixed behavior taxonomy plus retained legacy CSV labels.
- Prelabels `neg` videos as `normal_scene`; maps `dog_out` to `dog_enter_frame`.
- Produces structured `person_count` and `person_identity_attributes` fields for the companion person annotator.
- The companion annotator switches the original video per CSV row, previews event ranges, and writes only person fields.
- Requires a human review action before a row becomes reviewed.
- Supports legacy simple `start_time`/`end_time` CSVs and multi-event `events` CSVs.
- Creates a timestamped backup before the first in-process modification of an existing CSV and writes atomically.
- Uses no third-party dependencies, cloud services, or fabricated data.

## Evidence on Hand

The original multi-event and simple labeler scripts are in `D:\default file` until the verified replacement archives them under `old`. `D:\dapeng-test` contains 377 MP4 samples organized by behavior and `pos`/`neg` strata. No brand assets or visual identity materials are supplied.

## Product Principles

- Automated labels are hypotheses, never completed annotations.
- Preserve existing data and make every write recoverable.
- Keep repeated review actions fast: video, event cards, status, and navigation stay visible together.
- Favor a direct local tool over accounts, installation, or configuration systems.

## Accessibility & Inclusion

Keyboard playback and navigation remain available. Controls use native buttons, inputs, and selects with readable labels and visible focus states.
