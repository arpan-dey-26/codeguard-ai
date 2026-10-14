import type { ReactNode } from 'react'
import { cn } from '@/utils/cn'

/**
 * Small status label.
 *
 * Deliberately domain-agnostic: it knows nothing about severity or review
 * status. Domain meaning is applied by components in `components/domain`, so
 * this primitive stays reusable (architecture.md §20.1).
 */
export function Badge({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-border px-2 py-[2px]',
        'text-[11px] font-medium uppercase tracking-wide',
        className,
      )}
    >
      {children}
    </span>
  )
}
