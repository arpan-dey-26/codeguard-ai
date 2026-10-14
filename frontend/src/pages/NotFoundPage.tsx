import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <h1 className="text-xl font-semibold">Page not found</h1>
      <p className="mt-2 text-sm text-muted">That route does not exist.</p>
      <Link to="/" className="mt-4 inline-block text-sm text-accent underline">
        Back to dashboard
      </Link>
    </div>
  )
}
