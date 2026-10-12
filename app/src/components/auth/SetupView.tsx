"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import { useMe } from "@/components/shell/AuthGate"
import { Button } from "@/components/ui/Button"
import { Checkbox, Radio, Select } from "@/components/ui/controls"
import { Icon } from "@/components/ui/Icon"
import { api } from "@/lib/opencode/client"
import { PLACE_ORDER, PLACES } from "@/lib/symphony/places"
import { customCategory } from "@/lib/symphony/settings"
import type { Settings } from "@/lib/symphony/types"
import { Brand, ErrorNote, Field, message } from "./shared"
import styles from "./auth.module.css"

const BUSINESS_TYPES = ["Bakery and café", "Restaurant or food truck", "Retail shop", "Salon or spa", "Office or services", "Other"]

const HINTS: Record<string, string> = {
  customers: "Names, emails, phone numbers, order history",
  staff: "Schedules, pay, timesheets",
  money: "Card numbers, bank accounts, tax IDs",
  contracts: "Leases, supplier and catering agreements",
  recipes: "Your own recipes and methods",
  suppliers: "What you pay each vendor",
}

const STEPS = ["Your business", "Privacy", "Monthly limit"]

/**
 * First run, once the owner has signed in: the three things Symphony needs
 * to know before the first job.
 */
export function SetupView() {
  const { me } = useMe()
  if (!me) return <Shell>{null}</Shell>
  if (me.state === "signed-in" && me.user.role === "owner" && !me.onboarded) return <Onboarding name={me.user.name} />
  // Anyone else is sent on by the auth gate.
  return <Shell>{null}</Shell>
}

function Shell({ children, wide }: { children: React.ReactNode; wide?: boolean }) {
  return (
    <div className={wide ? `${styles.wrap} ${styles.wide}` : styles.wrap}>
      <Brand />
      {children}
    </div>
  )
}

function Onboarding({ name }: { name: string }) {
  const router = useRouter()
  const { refresh } = useMe()
  const [step, setStep] = useState(0)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [newItem, setNewItem] = useState("")
  const [limitText, setLimitText] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api<Settings>("/api/symphony", "/settings")
      .then((s) => {
        setSettings(s)
        setLimitText(String(s.spending.limit))
      })
      .catch((e) => setError(message(e)))
  }, [])

  const set = (fn: (s: Settings) => Settings) => setSettings((s) => (s ? fn(s) : s))

  function addItem() {
    const label = newItem.trim()
    if (!label || !settings) return
    const item = customCategory(label)
    if (!settings.keepPrivate.some((c) => c.id === item.id || c.label.toLowerCase() === label.toLowerCase())) {
      set((s) => ({ ...s, keepPrivate: [...s.keepPrivate, item] }))
    }
    setNewItem("")
  }

  const limit = Number(limitText.replace(/[$,\s]/g, ""))
  const limitOk = limitText.trim() !== "" && Number.isFinite(limit) && limit >= 0

  async function finish() {
    if (!settings) return
    setBusy(true)
    setError(null)
    try {
      await api("/api/symphony", "/settings", {
        method: "PUT",
        body: JSON.stringify({ ...settings, spending: { ...settings.spending, limit: Math.round(limit * 100) / 100 }, onboarded: true }),
      })
      await refresh()
      router.replace("/")
    } catch (e) {
      setError(message(e))
      setBusy(false)
    }
  }

  const foodPlace = settings ? /bak|caf|coffee|restaurant|food/i.test(settings.businessType) : false
  const canContinue = settings && (step !== 0 || settings.businessName.trim()) && (step !== 2 || limitOk)

  return (
    <Shell wide>
      <form
        className={styles.card}
        onSubmit={(e) => {
          e.preventDefault()
          if (!canContinue) return
          if (step < STEPS.length - 1) setStep(step + 1)
          else void finish()
        }}
      >
        <ol className={styles.steps} aria-label="Setup steps">
          {STEPS.map((label, i) => (
            <li key={label} className={i === step ? styles.stepOn : i < step ? styles.stepDone : undefined} aria-current={i === step ? "step" : undefined}>
              {i + 1}. {label}
            </li>
          ))}
        </ol>

        <ErrorNote>{error}</ErrorNote>

        {!settings ? (
          <p className={styles.muted}>Loading</p>
        ) : step === 0 ? (
          <div className={styles.form} style={{ maxWidth: 480 }}>
            <div>
              <h1>Hi {name.split(" ")[0]}. Tell us about your business.</h1>
              <p className={styles.muted}>Symphony uses this to suggest jobs and pick what to keep private.</p>
            </div>
            <Field
              label="Business name"
              required
              maxLength={80}
              autoComplete="organization"
              value={settings.businessName}
              onChange={(e) => set((s) => ({ ...s, businessName: e.target.value }))}
            />
            <label className={styles.field}>
              <span className={styles.label}>What kind of business</span>
              <Select value={settings.businessType} onChange={(e) => set((s) => ({ ...s, businessType: e.target.value }))}>
                {(BUSINESS_TYPES.includes(settings.businessType) ? BUSINESS_TYPES : [settings.businessType, ...BUSINESS_TYPES]).map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </Select>
            </label>
          </div>
        ) : step === 1 ? (
          <div className={styles.columns}>
            <div>
              <div>
                <h1>What should never leave your shop?</h1>
                <p className={styles.muted}>
                  {foodPlace ? "We picked these for bakeries and cafés." : "We picked the usual ones."} Jobs that mention anything you check stay{" "}
                  {PLACES.device.short} or {PLACES.private.short}. You can change this later.
                </p>
              </div>
              <ul className={styles.keepList}>
                {settings.keepPrivate.map((c) => (
                  <li key={c.id}>
                    <Checkbox
                      checked={c.on}
                      onChange={(on) => set((s) => ({ ...s, keepPrivate: s.keepPrivate.map((x) => (x.id === c.id ? { ...x, on } : x)) }))}
                    >
                      <span style={{ display: "flex", flexDirection: "column" }}>
                        <span>{c.label}</span>
                        {HINTS[c.id] ? <span className={styles.hint}>{HINTS[c.id]}</span> : null}
                      </span>
                    </Checkbox>
                    {c.custom ? (
                      <Button
                        variant="icon"
                        aria-label={`Remove ${c.label}`}
                        onClick={() => set((s) => ({ ...s, keepPrivate: s.keepPrivate.filter((x) => x.id !== c.id) }))}
                      >
                        <Icon name="close" size={14} />
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
              <div className={styles.field}>
                <label htmlFor="new-private" className={styles.label}>
                  Anything else?
                </label>
                <div className={styles.inline}>
                  <input
                    id="new-private"
                    className={styles.input}
                    placeholder="Like “wedding orders”"
                    maxLength={60}
                    value={newItem}
                    onChange={(e) => setNewItem(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault()
                        addItem()
                      }
                    }}
                  />
                  <Button disabled={!newItem.trim()} onClick={addItem}>
                    Add
                  </Button>
                </div>
              </div>
            </div>
            <aside className={styles.side} aria-label="How Symphony decides where work happens">
              <h3>How Symphony decides where work happens</h3>
              {PLACE_ORDER.map((p) => (
                <div key={p} className={styles.placeRow}>
                  <Icon name={PLACES[p].icon} size={16} style={{ color: PLACES[p].color }} />
                  <p>
                    <strong style={{ color: PLACES[p].ink }}>{PLACES[p].name}.</strong> <span className={styles.muted}>{PLACES[p].promise}</span>
                  </p>
                </div>
              ))}
              <p className={styles.hint}>It always tries the most private place first: Soloist on the box, then Orchestra in your private cloud for big jobs and the web.</p>
            </aside>
          </div>
        ) : (
          <div className={styles.form} style={{ maxWidth: 520 }}>
            <div>
              <h1>How much may Symphony spend each month?</h1>
              <p className={styles.muted}>
                Only Orchestra jobs cost money. Jobs {PLACES.device.short} are free. You&apos;ll see the running total under Privacy and spending.
              </p>
            </div>
            <label className={styles.field}>
              <span className={styles.label}>Monthly limit</span>
              <span className={styles.money}>
                <span aria-hidden="true">$</span>
                <input
                  className={styles.input}
                  inputMode="decimal"
                  aria-label="Monthly limit in dollars"
                  value={limitText}
                  onChange={(e) => setLimitText(e.target.value)}
                />
              </span>
              {!limitOk ? <span className={styles.hint}>Enter an amount in dollars, like 40.</span> : null}
            </label>
            <fieldset className={styles.options}>
              <legend>When the limit is reached</legend>
              <Radio
                name="at-limit"
                checked={settings.spending.atLimit === "pause"}
                onChange={() => set((s) => ({ ...s, spending: { ...s.spending, atLimit: "pause" } }))}
              >
                Pause Orchestra jobs until next month
              </Radio>
              <Radio
                name="at-limit"
                checked={settings.spending.atLimit === "device-only"}
                onChange={() => set((s) => ({ ...s, spending: { ...s.spending, atLimit: "device-only" } }))}
              >
                Keep going, but only {PLACES.device.short} (free)
              </Radio>
            </fieldset>
          </div>
        )}

        <div className={styles.actions}>
          {step > 0 ? (
            <Button variant="text" onClick={() => setStep(step - 1)} disabled={busy}>
              Back
            </Button>
          ) : null}
          <Button type="submit" variant="primary" disabled={!canContinue || busy}>
            {step < STEPS.length - 1 ? "Continue" : busy ? "Finishing" : "Finish setup"}
          </Button>
        </div>
      </form>
    </Shell>
  )
}
