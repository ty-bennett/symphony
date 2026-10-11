"use client"

import Link from "next/link"
import { useCallback, useEffect, useRef, useState } from "react"
import { Button } from "@/components/ui/Button"
import { Checkbox, Radio, Select, Switch } from "@/components/ui/controls"
import { Icon } from "@/components/ui/Icon"
import { PlaceLabel } from "@/components/ui/PlaceLabel"
import { api } from "@/lib/opencode/client"
import type { ProvidersResponse } from "@/lib/opencode/types"
import { formatCost, PLACE_ORDER, PLACES } from "@/lib/symphony/places"
import { describeRates, formatSaved, TYPICAL_RATES } from "@/lib/symphony/savings"
import { customCategory } from "@/lib/symphony/settings"
import type { Job, OpenCloudRule, Place, Savings, Settings, Spending } from "@/lib/symphony/types"
import styles from "./page.module.css"

const message = (e: unknown) => (e instanceof Error ? e.message : String(e))

const RULES: { id: OpenCloudRule; label: string }[] = [
  { id: "ask-if-private", label: "Ask me if a job includes something private" },
  { id: "ask-always", label: "Ask me before every job" },
  { id: "never", label: "Never send anything private" },
]

type SaveState = "idle" | "saving" | "saved" | "error"

export function PrivacyView() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [providers, setProviders] = useState<ProvidersResponse | null>(null)
  const [jobs, setJobs] = useState<Job[] | null>(null)
  const [money, setMoney] = useState<Spending | null>(null)
  const [savings, setSavings] = useState<Savings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [save, setSave] = useState<SaveState>("idle")
  const [newItem, setNewItem] = useState("")
  const [limitText, setLimitText] = useState("")
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const latest = useRef<Settings | null>(null)

  const loadJobs = useCallback(async () => {
    try {
      const data = await api<{ jobs: Job[]; spending: Spending; savings: Savings }>("/api/symphony", "/jobs")
      setJobs(data.jobs)
      setMoney(data.spending)
      setSavings(data.savings)
    } catch (e) {
      setError(message(e))
    }
  }, [])

  useEffect(() => {
    api<Settings>("/api/symphony", "/settings")
      .then((s) => {
        setSettings(s)
        setLimitText(String(s.spending.limit))
      })
      .catch((e) => setError(message(e)))
    api<ProvidersResponse>("/api/oc", "/providers")
      .then(setProviders)
      .catch(() => setProviders({ providers: [], default: {} }))
    void loadJobs()
  }, [loadJobs])

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current)
    },
    [],
  )

  const persist = useCallback(async () => {
    const body = latest.current
    if (!body) return
    try {
      const saved = await api<Settings>("/api/symphony", "/settings", { method: "PUT", body: JSON.stringify(body) })
      if (latest.current === body) setSave("saved")
      setMoney((m) => (m ? { ...m, limit: saved.spending.limit, atLimit: saved.spending.atLimit } : m))
      // Model choices change what "saved" is measured against.
      void loadJobs()
    } catch (e) {
      setSave("error")
      setError(`Couldn't save your changes. ${message(e)}`)
    }
  }, [loadJobs])

  function update(change: (s: Settings) => Settings) {
    setSettings((current) => {
      if (!current) return current
      const next = change(current)
      latest.current = next
      return next
    })
    setSave("saving")
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(() => void persist(), 400)
  }

  function addItem() {
    const label = newItem.trim()
    if (!label || !settings) return
    const item = customCategory(label)
    if (settings.keepPrivate.some((c) => c.id === item.id || c.label.toLowerCase() === label.toLowerCase())) {
      setNewItem("")
      return
    }
    update((s) => ({ ...s, keepPrivate: [...s.keepPrivate, item] }))
    setNewItem("")
  }

  function commitLimit() {
    const value = Number(limitText.replace(/[$,\s]/g, ""))
    if (!Number.isFinite(value) || value < 0) {
      setLimitText(String(settings?.spending.limit ?? ""))
      return
    }
    const rounded = Math.round(value * 100) / 100
    setLimitText(String(rounded))
    if (rounded !== settings?.spending.limit) update((s) => ({ ...s, spending: { ...s.spending, limit: rounded } }))
  }

  function downloadReport() {
    const compared = savings?.rates.label ?? "the open cloud"
    const rows = [
      ["Job", "Started", "Where it ran", "Why", "Model", "Tokens in", "Tokens out", "Cost (USD)", `Would have cost at ${compared} prices (USD)`, "Saved (USD)"],
    ]
    for (const job of jobs ?? []) {
      rows.push([
        job.title,
        new Date(job.created).toISOString(),
        job.place ? PLACES[job.place].name : "Unknown",
        job.reason ?? "",
        job.model ?? "",
        String(job.tokens ? job.tokens.input + job.tokens.cacheRead + job.tokens.cacheWrite : ""),
        String(job.tokens ? job.tokens.output + job.tokens.reasoning : ""),
        job.cost.toFixed(4),
        job.wouldHaveCost === undefined ? "" : job.wouldHaveCost.toFixed(4),
        job.saved === undefined ? "" : job.saved.toFixed(4),
      ])
    }
    const csv = rows.map((r) => r.map((cell) => `"${cell.replace(/"/g, '""')}"`).join(",")).join("\r\n")
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }))
    const a = document.createElement("a")
    a.href = url
    a.download = `symphony-privacy-report-${new Date().toISOString().slice(0, 10)}.csv`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const models = (providers?.providers ?? []).flatMap((p) =>
    Object.values(p.models).map((m) => ({ value: `${p.id}/${m.id}`, label: m.name, provider: p.name })),
  )

  const priced = (providers?.providers ?? []).flatMap((p) =>
    Object.values(p.models)
      .filter((m) => m.cost)
      .map((m) => ({ value: `${p.id}/${m.id}`, label: `${m.name} ($${m.cost!.input} in, $${m.cost!.output} out)`, provider: p.name })),
  )

  function modelSelect(place: Place, value: string, disabled: boolean) {
    const known = !value || models.some((m) => m.value === value)
    const groups = Array.from(new Set(models.map((m) => m.provider)))
    return (
      <label className={styles.field} style={{ width: "min(260px, 100%)" }}>
        <span className={styles.fieldLabel}>Model</span>
        <Select
          value={value}
          disabled={disabled}
          onChange={(e) =>
            update((s) => ({ ...s, places: { ...s.places, [place]: { ...s.places[place], model: e.target.value } } }))
          }
        >
          <option value="">opencode&apos;s default</option>
          {known ? null : <option value={value}>{value}</option>}
          {groups.map((g) => (
            <optgroup key={g} label={g}>
              {models
                .filter((m) => m.provider === g)
                .map((m) => (
                  <option key={m.value} value={m.value}>
                    {m.label}
                  </option>
                ))}
            </optgroup>
          ))}
        </Select>
      </label>
    )
  }

  const month = new Date().toLocaleDateString(undefined, { month: "long" })
  const total = money?.total ?? 0
  const limit = settings?.spending.limit ?? 0
  const share = (n: number) => (limit > 0 ? Math.min(100, (n / limit) * 100) : 0)
  const thisMonth = (jobs ?? []).filter((j) => {
    const d = new Date(j.created)
    const now = new Date()
    return d.getMonth() === now.getMonth() && d.getFullYear() === now.getFullYear()
  })

  return (
    <div className={`${styles.page} ${styles.narrow}`}>
      <header className={styles.header}>
        <div className={styles.headerText}>
          <h1>Privacy and spending</h1>
          <p className={styles.muted}>Decide what stays in the building, and what Symphony may spend.</p>
        </div>
        <p className={styles.saved} role="status" aria-live="polite">
          {save === "saving" ? "Saving" : save === "saved" ? "Saved" : save === "error" ? "Not saved" : ""}
        </p>
      </header>

      {error ? (
        <div className={styles.error} role="alert">
          <Icon name="alert" size={16} />
          <p style={{ flex: 1 }}>{error}</p>
          <Button variant="icon" aria-label="Dismiss" onClick={() => setError(null)}>
            <Icon name="close" size={16} />
          </Button>
        </div>
      ) : null}

      {!settings ? (
        <p className={styles.muted}>Loading</p>
      ) : (
        <>
          <section className={styles.section} aria-labelledby="places-h">
            <h2 id="places-h">Where jobs can run</h2>
            <p className={styles.muted}>Symphony always tries the most private place first.</p>
            <ul className={styles.list}>
              {PLACE_ORDER.map((place) => {
                const p = PLACES[place]
                const enabled = place === "device" || settings.places[place].enabled
                return (
                  <li key={place} className={styles.settingRow}>
                    <Icon name={p.icon} size={20} style={{ color: p.color }} />
                    <div className={styles.settingText}>
                      <h3>{p.name}</h3>
                      <p className={styles.muted}>{p.promise}</p>
                    </div>
                    <div className={styles.settingControls} style={{ alignItems: "flex-end", justifyContent: "flex-end" }}>
                      {modelSelect(place, settings.places[place].model, !enabled)}
                      <span style={{ width: 64, minHeight: "var(--target)", display: "flex", alignItems: "center", justifyContent: "flex-end" }}>
                      {place === "device" ? (
                        <span className={styles.meta} style={{ textAlign: "right" }}>
                          Always on
                        </span>
                      ) : (
                        <Switch
                          checked={enabled}
                          label={`Use the ${p.name.toLowerCase()}`}
                          hideLabel
                          onChange={(on) =>
                            update((s) => ({ ...s, places: { ...s.places, [place]: { ...s.places[place], enabled: on } } }))
                          }
                        />
                      )}
                      </span>
                    </div>
                    {place === "open" && enabled ? (
                      <label className={styles.field} style={{ flexBasis: "100%", paddingLeft: 36, maxWidth: 480 }}>
                        <span className={styles.fieldLabel}>Before sending anything private here</span>
                        <Select
                          value={settings.places.open.rule}
                          onChange={(e) =>
                            update((s) => ({
                              ...s,
                              places: { ...s.places, open: { ...s.places.open, rule: e.target.value as OpenCloudRule } },
                            }))
                          }
                        >
                          {RULES.map((r) => (
                            <option key={r.id} value={r.id}>
                              {r.label}
                            </option>
                          ))}
                        </Select>
                      </label>
                    ) : null}
                  </li>
                )
              })}
            </ul>
          </section>

          <section className={styles.section} aria-labelledby="keep-h">
            <h2 id="keep-h">Always keep private</h2>
            <p className={styles.muted}>
              Jobs that mention these stay {PLACES.device.short} or {PLACES.private.short}. Email addresses and phone numbers count as
              customer contact info.
            </p>
            <ul className={styles.chips} style={{ marginTop: 8 }}>
              {settings.keepPrivate.map((c) => (
                <li key={c.id}>
                  <Checkbox
                    checked={c.on}
                    onChange={(on) => update((s) => ({ ...s, keepPrivate: s.keepPrivate.map((x) => (x.id === c.id ? { ...x, on } : x)) }))}
                  >
                    {c.label}
                  </Checkbox>
                  {c.custom ? (
                    <Button
                      variant="icon"
                      aria-label={`Remove ${c.label}`}
                      onClick={() => update((s) => ({ ...s, keepPrivate: s.keepPrivate.filter((x) => x.id !== c.id) }))}
                    >
                      <Icon name="close" size={14} />
                    </Button>
                  ) : null}
                </li>
              ))}
            </ul>
            <form
              className={styles.inline}
              style={{ marginTop: 8 }}
              onSubmit={(e) => {
                e.preventDefault()
                addItem()
              }}
            >
              <label htmlFor="new-private" className="sr-only">
                Something else to keep private
              </label>
              <input
                id="new-private"
                className={styles.textInput}
                placeholder="Something else, like “wedding orders”"
                value={newItem}
                maxLength={60}
                onChange={(e) => setNewItem(e.target.value)}
                style={{ flex: "1 1 240px", maxWidth: 360 }}
              />
              <Button type="submit" variant="secondary" disabled={!newItem.trim()}>
                Add
              </Button>
            </form>
          </section>

          <section className={styles.section} aria-labelledby="spend-h">
            <h2 id="spend-h">Spending in {month}</h2>
            <p>
              <span className={styles.big}>{formatCost(total)}</span>{" "}
              <span className={styles.muted}>of your {formatCost(limit)} limit</span>
            </p>
            <div
              className={styles.bar}
              role="img"
              aria-label={`${formatCost(total)} spent of ${formatCost(limit)}`}
              style={{ margin: "8px 0" }}
            >
              {PLACE_ORDER.map((place) =>
                money && money.byPlace[place] > 0 ? (
                  <span key={place} style={{ width: `${share(money.byPlace[place])}%`, background: PLACES[place].color }} />
                ) : null,
              )}
            </div>
            <ul className={styles.legend}>
              {PLACE_ORDER.map((place) => (
                <li key={place}>
                  <span className={styles.swatch} style={{ background: PLACES[place].color }} aria-hidden="true" />
                  <span>{PLACES[place].name}</span>
                  <span className={styles.muted} style={{ fontVariantNumeric: "lining-nums tabular-nums" }}>
                    {formatCost(money?.byPlace[place] ?? 0)}
                  </span>
                </li>
              ))}
            </ul>
            {total >= limit && limit > 0 ? (
              <p className={styles.warnText}>
                You&apos;ve reached your limit.{" "}
                {settings.spending.atLimit === "pause" ? "New cloud jobs are paused." : `New jobs run ${PLACES.device.short}.`}
              </p>
            ) : null}

            <div className={styles.settingRow} style={{ alignItems: "flex-start", borderTop: "1px solid var(--line)", marginTop: 16 }}>
              <label className={styles.field}>
                <span className={styles.fieldLabel}>Monthly limit (USD)</span>
                <input
                  className={styles.textInput}
                  inputMode="decimal"
                  value={limitText}
                  onChange={(e) => setLimitText(e.target.value)}
                  onBlur={commitLimit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitLimit()
                  }}
                  style={{ width: 140 }}
                />
              </label>
              <fieldset className={styles.options} style={{ flex: "1 1 300px" }}>
                <legend className={styles.fieldLabel} style={{ fontWeight: 400 }}>
                  When the limit is reached
                </legend>
                <Radio
                  name="at-limit"
                  checked={settings.spending.atLimit === "pause"}
                  onChange={() => update((s) => ({ ...s, spending: { ...s.spending, atLimit: "pause" } }))}
                >
                  Pause cloud jobs until next month
                </Radio>
                <Radio
                  name="at-limit"
                  checked={settings.spending.atLimit === "device-only"}
                  onChange={() => update((s) => ({ ...s, spending: { ...s.spending, atLimit: "device-only" } }))}
                >
                  Keep going, but only {PLACES.device.short} (free)
                </Radio>
              </fieldset>
            </div>
          </section>

          <section className={styles.section} aria-labelledby="saved-h" id="saved">
            <h2 id="saved-h">Saved in {month}</h2>
            {!savings ? (
              <p className={styles.muted}>Loading</p>
            ) : savings.jobs === 0 ? (
              <p className={styles.muted}>
                Nothing yet this month. Jobs that run {PLACES.device.short} or {PLACES.private.short} count here.
              </p>
            ) : (
              <>
                <p>
                  <span className={styles.big} style={savings.saved < 0 ? undefined : { color: "var(--good)" }}>
                    {formatSaved(savings.saved)}
                  </span>{" "}
                  <span className={styles.muted}>
                    {savings.saved < 0 ? "more than the open cloud" : "saved"} by keeping {savings.jobs}{" "}
                    {savings.jobs === 1 ? "job" : "jobs"} off the open cloud
                  </span>
                </p>
                <p>
                  At {savings.rates.label} prices, {savings.jobs === 1 ? "that job" : "those jobs"} would have cost{" "}
                  <strong>{formatCost(savings.wouldHaveCost)}</strong>. {savings.jobs === 1 ? "It" : "They"} cost{" "}
                  <strong>{formatCost(savings.actual)}</strong>.
                </p>
                <ul className={styles.legend} style={{ marginTop: 4 }}>
                  {PLACE_ORDER.filter((p) => p !== "open").map((place) => (
                    <li key={place}>
                      <span className={styles.swatch} style={{ background: PLACES[place].color }} aria-hidden="true" />
                      <span>{PLACES[place].name}</span>
                      <span className={styles.muted} style={{ fontVariantNumeric: "lining-nums tabular-nums" }}>
                        {formatSaved(savings.byPlace[place])}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className={styles.meta}>Since you started: {formatSaved(savings.allTime)}</p>
              </>
            )}

            <div className={styles.settingRow} style={{ alignItems: "flex-start", borderTop: "1px solid var(--line)", marginTop: 16 }}>
              <label className={styles.field} style={{ width: "min(420px, 100%)" }}>
                <span className={styles.fieldLabel}>Compare with</span>
                <Select
                  value={settings.savings.compareWith}
                  onChange={(e) => update((s) => ({ ...s, savings: { compareWith: e.target.value } }))}
                >
                  <option value="open">Your open-cloud model</option>
                  <option value="typical">
                    A typical frontier model (${TYPICAL_RATES.input} in, ${TYPICAL_RATES.output} out)
                  </option>
                  {priced.some((m) => m.value === settings.savings.compareWith) ||
                  ["open", "typical"].includes(settings.savings.compareWith) ? null : (
                    <option value={settings.savings.compareWith}>{settings.savings.compareWith}</option>
                  )}
                  {Array.from(new Set(priced.map((m) => m.provider))).map((g) => (
                    <optgroup key={g} label={g}>
                      {priced
                        .filter((m) => m.provider === g)
                        .map((m) => (
                          <option key={m.value} value={m.value}>
                            {m.label}
                          </option>
                        ))}
                    </optgroup>
                  ))}
                </Select>
              </label>
              <div className={styles.settingText} style={{ flexBasis: 260 }}>
                {savings ? (
                  <p className={styles.meta} style={{ paddingTop: 22 }}>
                    {settings.savings.compareWith === "open" && savings.rates.typical
                      ? `Your open-cloud model has no price in opencode, so this uses typical frontier prices (${describeRates(savings.rates)}).`
                      : `Priced at ${describeRates(savings.rates)}.`}{" "}
                    An estimate from the tokens each job used. It doesn&apos;t count the Symphony box or its electricity.
                  </p>
                ) : null}
              </div>
            </div>
          </section>

          <section className={styles.section} aria-labelledby="went-h">
            <div className={styles.actions}>
              <h2 id="went-h" style={{ fontSize: "var(--fs-section)", lineHeight: 1.25 }}>
                Where every job went
              </h2>
              <Button variant="secondary" disabled={!jobs?.length} onClick={downloadReport}>
                <Icon name="download" size={16} />
                Download privacy report
              </Button>
            </div>
            <p className={styles.muted}>This month, newest first. The report has every job.</p>
            {jobs === null ? (
              <p className={styles.muted}>Loading</p>
            ) : thisMonth.length === 0 ? (
              <p className={styles.muted}>No jobs yet this month.</p>
            ) : (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th scope="col">Job</th>
                      <th scope="col">Where</th>
                      <th scope="col">Why</th>
                      <th scope="col" className={styles.num}>
                        Cost
                      </th>
                      <th scope="col" className={styles.num}>
                        Saved
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {thisMonth.map((job) => (
                      <tr key={job.id}>
                        <td>
                          <Link href={`/work/${job.id}`}>{job.title}</Link>
                          <div className={styles.meta}>
                            {new Date(job.created).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                          </div>
                        </td>
                        <td>
                          <PlaceLabel place={job.place} quiet />
                        </td>
                        <td className={styles.muted}>
                          {job.reason ? job.reason.charAt(0).toUpperCase() + job.reason.slice(1) : "Started from the coding agent"}
                        </td>
                        <td className={styles.num}>{formatCost(job.cost)}</td>
                        <td className={styles.num} style={{ color: job.saved ? "var(--good)" : "var(--muted)" }}>
                          {job.saved === undefined || job.place === "open" ? "—" : formatSaved(job.saved)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
