'use client';

import { cn } from '../lib/cn';

import type { Budget, TurnDetectorData } from './selection';
import { formatMetric, metricKindForBudget, rankingForBudget } from './selection';

const tokenColor = (token: string) => `oklch(var(--lk-color-${token}))`;

interface RankingTableProps {
  data: TurnDetectorData;
  budget: Budget;
  activeModelKey?: string | null;
  onActiveModelChange?: (key: string | null) => void;
}

export function RankingTable({
  data,
  budget,
  activeModelKey,
  onActiveModelChange,
}: RankingTableProps) {
  const kind = metricKindForBudget(budget);
  const rows = rankingForBudget(data, budget);
  const values = rows.map((r) => r.value).filter((v): v is number => v !== null);
  // Bar length is proportional to the value itself (shared fixed scale), so it
  // reads the same as the number next to it. Lower is better → shorter bar.
  const maxValue = values.length ? Math.max(...values) : 1;

  const metricHeader = kind === 'cutoff' ? 'False cut-off rate' : 'Mean latency';

  return (
    <table className="w-full table-fixed border-collapse text-sm">
      <colgroup>
        <col className="w-8" />
        <col />
        <col className="w-40" />
      </colgroup>
      <thead>
        <tr className="text-fg3 border-separator1 border-b text-left font-mono text-xs tracking-wider uppercase">
          <th className="py-2 pr-2 font-medium">#</th>
          <th className="py-2 pr-2 font-medium">Model</th>
          <th className="py-2 pl-2 text-right font-medium">{metricHeader}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => {
          const isActive = activeModelKey === row.model.key;
          const isInteractive = onActiveModelChange != null;
          const barWidth = row.value === null ? 0 : (row.value / maxValue) * 100;
          return (
            <tr
              key={row.model.key}
              className={cn(
                'border-separator1 h-12 border-b transition-colors',
                isActive && 'bg-bg2',
                isInteractive && 'hover:bg-bg2 cursor-pointer',
              )}
              onMouseEnter={isInteractive ? () => onActiveModelChange(row.model.key) : undefined}
              onMouseLeave={isInteractive ? () => onActiveModelChange(null) : undefined}
            >
              <td className="text-fg3 pr-2 align-middle font-mono tabular-nums">{index + 1}</td>
              <td className="pr-2 align-middle">
                <span className="flex min-w-0 items-center gap-2">
                  <span
                    aria-hidden
                    className="inline-block h-2.5 w-2.5 shrink-0 rounded-full"
                    style={{ backgroundColor: tokenColor(row.model.colorToken) }}
                  />
                  <span
                    title={row.model.label}
                    className={cn(
                      'line-clamp-2 min-w-0 leading-tight',
                      row.model.isLiveKit ? 'text-fg0 font-semibold' : 'text-fg1',
                    )}
                  >
                    {row.model.label}
                  </span>
                </span>
              </td>
              <td className="pl-2 align-middle">
                <span className="flex items-center justify-end gap-2">
                  <span
                    aria-hidden
                    className="bg-bg3 h-1.5 min-w-0 flex-1 overflow-hidden rounded-full"
                  >
                    <span
                      className="block h-full rounded-full"
                      style={{
                        width: `${barWidth}%`,
                        backgroundColor: tokenColor(row.model.colorToken),
                      }}
                    />
                  </span>
                  <span
                    className={cn(
                      'w-16 shrink-0 whitespace-nowrap text-right font-mono tabular-nums',
                      row.value === null ? 'text-fg4' : 'text-fg1',
                    )}
                  >
                    {formatMetric(row.value, kind)}
                  </span>
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
