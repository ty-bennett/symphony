// Server-only: owner settings live in a small JSON file on the box.
import { mkdir, readFile, rename, writeFile } from "node:fs/promises"
import path from "node:path"
import { DEFAULT_SETTINGS, normalizeSettings } from "./settings"
import type { Settings } from "./types"

export function dataDir() {
  return process.env.SYMPHONY_DATA_DIR || path.join(process.cwd(), ".symphony")
}

function settingsFile() {
  return path.join(dataDir(), "settings.json")
}

export async function readSettings(): Promise<Settings> {
  try {
    return normalizeSettings(JSON.parse(await readFile(settingsFile(), "utf8")))
  } catch {
    return DEFAULT_SETTINGS
  }
}

export async function writeSettings(input: unknown): Promise<Settings> {
  const settings = normalizeSettings(input)
  await mkdir(dataDir(), { recursive: true })
  // Write then rename, so a crash never leaves a half-written file.
  const tmp = `${settingsFile()}.${process.pid}.tmp`
  await writeFile(tmp, JSON.stringify(settings, null, 2))
  await rename(tmp, settingsFile())
  return settings
}
