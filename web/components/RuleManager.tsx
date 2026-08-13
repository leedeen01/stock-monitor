"use client";

import { useActionState, useState, useTransition } from "react";
import { useFormStatus } from "react-dom";

import {
  type ActionResult,
  createRule,
  deleteRule,
  evaluateNow,
  toggleRule,
} from "@/app/alert-actions";
import { METRICS } from "@/lib/metrics";
import type { AlertRule, GroupRef, WatchlistRow } from "@/lib/queries";

const CONDITIONS = [
  { value: "percentile_below", label: "percentile below", needsThreshold: true },
  { value: "percentile_above", label: "percentile above", needsThreshold: true },
  { value: "value_below", label: "value below", needsThreshold: true },
  { value: "value_above", label: "value above", needsThreshold: true },
  { value: "new_filing", label: "new filing lands", needsThreshold: false },
];

export function RuleManager({
  rules,
  groups,
  tickers,
}: {
  rules: AlertRule[];
  groups: GroupRef[];
  tickers: WatchlistRow[];
}) {
  const [state, action] = useActionState<ActionResult | null, FormData>(
    createRule,
    null,
  );
  const [condition, setCondition] = useState("percentile_below");
  const [scope, setScope] = useState("all");
  const [pending, startTransition] = useTransition();
  const [notice, setNotice] = useState<string | null>(null);

  const needsThreshold =
    CONDITIONS.find((c) => c.value === condition)?.needsThreshold ?? true;

  return (
    <div className="space-y-8">
      <section>
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-medium">Rules</h2>
          <div className="flex items-center gap-3 text-xs">
            {notice && <span className="text-neutral-500">{notice}</span>}
            <button
              disabled={pending}
              onClick={() =>
                startTransition(async () => {
                  const result = await evaluateNow();
                  setNotice(result.message);
                })
              }
              className="rounded-md border border-neutral-300 px-2.5 py-1 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-900"
            >
              {pending ? "Checking…" : "Check now"}
            </button>
          </div>
        </div>

        <div className="overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
          <table className="w-full min-w-[720px] text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500 dark:border-neutral-800">
                <th className="px-3 py-2 font-medium">Rule</th>
                <th className="px-3 py-2 font-medium">Applies to</th>
                <th className="px-3 py-2 font-medium">Condition</th>
                <th className="px-3 py-2 text-right font-medium">Open</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <RuleRow key={rule.id} rule={rule} />
              ))}
              {rules.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-3 py-6 text-center text-neutral-400">
                    No rules yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium">New rule</h2>
        <form
          action={action}
          className="grid gap-3 rounded-lg border border-neutral-200 p-4 sm:grid-cols-2 dark:border-neutral-800"
        >
          <Field label="Name" className="sm:col-span-2">
            <input
              name="name"
              required
              placeholder="e.g. Semis getting expensive"
              className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            />
          </Field>

          <Field label="Applies to">
            <select
              name="scope"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            >
              <option value="all">All stocks</option>
              <option value="group">A group</option>
              <option value="ticker">One stock</option>
            </select>
          </Field>

          <Field label={scope === "all" ? " " : scope === "group" ? "Group" : "Ticker"}>
            {scope === "all" ? (
              <input type="hidden" name="scopeRef" value="" />
            ) : scope === "group" ? (
              <select
                name="scopeRef"
                className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
              >
                {groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name}
                  </option>
                ))}
              </select>
            ) : (
              <select
                name="scopeRef"
                className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
              >
                {tickers.map((t) => (
                  <option key={t.ticker} value={t.ticker}>
                    {t.ticker}
                  </option>
                ))}
              </select>
            )}
          </Field>

          <Field label="Metric">
            <select
              name="metricKey"
              defaultValue="__primary__"
              className="w-full rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
            >
              <option value="__primary__">
                Leading multiple (per group)
              </option>
              {METRICS.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Condition">
            <div className="flex gap-2">
              <select
                name="condition"
                value={condition}
                onChange={(e) => setCondition(e.target.value)}
                className="min-w-0 flex-1 rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm dark:border-neutral-700 dark:bg-neutral-950"
              >
                {CONDITIONS.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
              <input
                name="threshold"
                type="number"
                step="any"
                required={needsThreshold}
                disabled={!needsThreshold}
                placeholder={condition.startsWith("percentile") ? "0-100" : "value"}
                className="w-24 rounded-md border border-neutral-300 px-2.5 py-1.5 text-sm disabled:opacity-40 dark:border-neutral-700 dark:bg-neutral-950"
              />
            </div>
          </Field>

          <div className="sm:col-span-2">
            <Submit />
            {state?.message && (
              <p
                className={`mt-2 text-xs ${
                  state.ok
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-rose-600 dark:text-rose-400"
                }`}
              >
                {state.message}
              </p>
            )}
            <p className="mt-2 text-xs text-neutral-400">
              Rules fire on the session a condition is crossed, not every day it
              stays true — so a stock parked below its 15th percentile alerts once,
              then stays quiet until it crosses back.
            </p>
          </div>
        </form>
      </section>
    </div>
  );
}

function RuleRow({ rule }: { rule: AlertRule }) {
  const [pending, startTransition] = useTransition();
  const [gone, setGone] = useState(false);
  const [armed, setArmed] = useState(false);
  if (gone) return null;

  const conditionLabel =
    CONDITIONS.find((c) => c.value === rule.condition)?.label ?? rule.condition;
  const metricLabel =
    rule.metricKey === "__primary__"
      ? "Leading multiple"
      : (METRICS.find((m) => m.key === rule.metricKey)?.label ?? rule.metricKey);

  return (
    <tr
      className={`border-b border-neutral-100 last:border-0 dark:border-neutral-900 ${
        rule.enabled ? "" : "opacity-50"
      }`}
    >
      <td className="px-3 py-2.5">{rule.name}</td>
      <td className="px-3 py-2.5 text-neutral-500">{rule.scopeLabel}</td>
      <td className="px-3 py-2.5 text-neutral-600 dark:text-neutral-400">
        {metricLabel} {conditionLabel}
        {rule.threshold !== null && (
          <span className="font-mono"> {rule.threshold}</span>
        )}
      </td>
      <td className="px-3 py-2.5 text-right font-mono tabular-nums">
        {rule.openCount || "—"}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-right">
        <button
          disabled={pending}
          onClick={() =>
            startTransition(async () => {
              await toggleRule(rule.id, !rule.enabled);
            })
          }
          className="mr-2 text-xs text-neutral-500 hover:text-neutral-900 disabled:opacity-50 dark:hover:text-neutral-100"
        >
          {rule.enabled ? "Pause" : "Enable"}
        </button>
        {armed ? (
          <>
            <button
              disabled={pending}
              onClick={() =>
                startTransition(async () => {
                  setGone(true);
                  await deleteRule(rule.id);
                })
              }
              className="rounded bg-rose-600 px-1.5 py-0.5 text-[10px] text-white hover:bg-rose-700"
            >
              Delete
            </button>
            <button
              onClick={() => setArmed(false)}
              className="ml-1 text-[10px] text-neutral-500"
            >
              Cancel
            </button>
          </>
        ) : (
          <button
            onClick={() => setArmed(true)}
            className="text-xs text-neutral-400 hover:text-rose-600 dark:hover:text-rose-400"
          >
            Delete
          </button>
        )}
      </td>
    </tr>
  );
}

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs text-neutral-500">{label}</span>
      {children}
    </label>
  );
}

function Submit() {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-60 dark:bg-neutral-100 dark:text-neutral-900"
    >
      {pending ? "Creating…" : "Create rule"}
    </button>
  );
}
