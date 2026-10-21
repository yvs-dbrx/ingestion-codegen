import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Badge } from '../components/Badge'
import { JoinOrganizationForm } from '../components/JoinOrganizationForm'
import { ErrorBanner, FormField } from '../components/FormField'
import { ApiError, useAuth } from '../lib/auth'
import { account, team } from '../lib/endpoints'
import { formatDateTime } from '../lib/format'
import type { InviteCreatedOut, Role } from '../lib/types'

export function TeamPage() {
  const { token, activeOrgId, user, refreshUser } = useAuth()
  const queryClient = useQueryClient()
  const role = user?.memberships.find((m) => m.org.id === activeOrgId)?.role
  const isAdmin = role === 'admin'
  const enabled = Boolean(token && activeOrgId)

  const [error, setError] = useState<{ message: string; problems?: string[] } | null>(null)
  const [email, setEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<Role>('member')
  const [created, setCreated] = useState<InviteCreatedOut | null>(null)
  const [copied, setCopied] = useState(false)

  const fail = (err: unknown) =>
    setError(
      err instanceof ApiError
        ? { message: err.detail, problems: err.problems }
        : { message: 'Something went wrong — try again' },
    )

  const usageQuery = useQuery({
    queryKey: ['usage', activeOrgId],
    queryFn: () => account.usage(token!, activeOrgId!),
    enabled,
  })
  const membersQuery = useQuery({
    queryKey: ['members', activeOrgId],
    queryFn: () => team.members(token!, activeOrgId!),
    enabled,
  })
  const invitesQuery = useQuery({
    queryKey: ['invites', activeOrgId],
    queryFn: () => team.invites(token!, activeOrgId!),
    enabled: enabled && isAdmin,
  })

  async function reload() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['members', activeOrgId] }),
      queryClient.invalidateQueries({ queryKey: ['invites', activeOrgId] }),
      queryClient.invalidateQueries({ queryKey: ['usage', activeOrgId] }),
    ])
  }

  const invite = useMutation({
    mutationFn: () => team.invite(token!, activeOrgId!, { email, role: inviteRole }),
    onMutate: () => {
      setError(null)
      setCreated(null)
      setCopied(false)
    },
    onSuccess: async (result) => {
      setCreated(result)
      setEmail('')
      await reload()
    },
    onError: fail,
  })

  const revoke = useMutation({
    mutationFn: (inviteId: string) => team.revokeInvite(token!, activeOrgId!, inviteId),
    onMutate: () => setError(null),
    onSuccess: reload,
    onError: fail,
  })

  const remove = useMutation({
    mutationFn: (userId: string) => team.removeMember(token!, activeOrgId!, userId),
    onMutate: () => setError(null),
    onSuccess: async (_, userId) => {
      if (userId === user?.user_id) {
        // Left the organization: re-read memberships and move to another org if there is one.
        await refreshUser()
        window.location.assign('/')
        return
      }
      await reload()
    },
    onError: fail,
  })

  function handleInvite(e: FormEvent) {
    e.preventDefault()
    invite.mutate()
  }

  const inviteLink = created ? `${window.location.origin}/accept-invite?token=${encodeURIComponent(created.token)}` : ''

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(inviteLink)
      setCopied(true)
    } catch {
      setCopied(false) // clipboard blocked: the link is selectable on screen
    }
  }

  const usage = usageQuery.data
  const seatsFull = usage ? usage.member_count + usage.pending_invites >= usage.member_limit : false

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold text-text">Team</h1>
        {usage && (
          <p className="mt-1 text-sm text-text-muted">
            {usage.member_count} of {usage.member_limit} seats in use
            {usage.pending_invites > 0 && ` · ${usage.pending_invites} held by pending invitations`}
          </p>
        )}
      </div>

      {error && <ErrorBanner message={error.message} problems={error.problems} />}

      {isAdmin && (
        <section className="rounded-lg border border-border bg-surface p-5">
          <h2 className="font-medium text-text">Invite someone</h2>
          <form onSubmit={handleInvite} className="mt-3 flex flex-wrap items-end gap-3">
            <div className="min-w-60 flex-1">
              <FormField
                label="Email"
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-text">Role</span>
              <select
                className="rounded-md border border-border bg-surface px-3 py-2"
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value as Role)}
              >
                <option value="member">Member</option>
                <option value="admin">Admin</option>
              </select>
            </label>
            <button
              type="submit"
              disabled={invite.isPending || seatsFull}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-contrast hover:bg-accent-hover disabled:opacity-60"
            >
              {invite.isPending ? 'Creating…' : 'Create invitation'}
            </button>
          </form>
          {seatsFull && (
            <p className="mt-2 text-sm text-text-muted">
              All seats are taken (pending invitations count). Remove a member or revoke an invitation first.
            </p>
          )}

          {created && (
            <div className="mt-4 rounded-md border border-accent/60 bg-accent/10 p-4">
              <p className="text-sm font-medium text-text">Invitation created for {created.email}</p>
              <p className="mt-1 text-sm text-text-muted">
                Nothing is emailed — send them this link yourself. It's shown only now and can't be retrieved
                later (invite the same address again to get a new one).
              </p>
              <p className="mt-3 break-all rounded-md border border-border bg-bg p-2 font-mono text-xs text-text">
                {inviteLink}
              </p>
              <button
                onClick={copyLink}
                className="mt-3 rounded-md border border-border px-3 py-1.5 text-sm font-medium text-text hover:bg-border/60"
              >
                {copied ? 'Copied ✓' : 'Copy link'}
              </button>
            </div>
          )}
        </section>
      )}

      <section className="rounded-lg border border-border bg-surface">
        <h2 className="border-b border-border px-5 py-3 font-medium text-text">Members</h2>
        {membersQuery.isLoading && <p className="p-5 text-text-muted">Loading members…</p>}
        {membersQuery.data && (
          <table className="w-full text-left text-sm">
            <tbody>
              {membersQuery.data.map((m) => {
                const isMe = m.user_id === user?.user_id
                const canRemove = isAdmin || isMe
                return (
                  <tr key={m.user_id} className="border-b border-border last:border-0">
                    <td className="px-5 py-3 text-text">
                      {m.email}
                      {isMe && <span className="ml-2 text-xs text-text-muted">(you)</span>}
                    </td>
                    <td className="py-3 pr-4">
                      <Badge tone={m.role === 'admin' ? 'gold' : 'neutral'}>{m.role}</Badge>
                    </td>
                    <td className="py-3 pr-4 text-text-muted">Joined {formatDateTime(m.joined_at)}</td>
                    <td className="py-3 pr-5 text-right">
                      {canRemove && (
                        <button
                          onClick={() => {
                            const prompt = isMe
                              ? 'Leave this organization?'
                              : `Remove ${m.email} from this organization?`
                            if (window.confirm(prompt)) remove.mutate(m.user_id)
                          }}
                          disabled={remove.isPending}
                          className="text-sm text-danger hover:underline disabled:opacity-60"
                        >
                          {isMe ? 'Leave' : 'Remove'}
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {isAdmin && (
        <section className="rounded-lg border border-border bg-surface">
          <h2 className="border-b border-border px-5 py-3 font-medium text-text">Pending invitations</h2>
          {invitesQuery.data && invitesQuery.data.length === 0 && (
            <p className="p-5 text-sm text-text-muted">No pending invitations.</p>
          )}
          {invitesQuery.data && invitesQuery.data.length > 0 && (
            <table className="w-full text-left text-sm">
              <tbody>
                {invitesQuery.data.map((i) => (
                  <tr key={i.id} className="border-b border-border last:border-0">
                    <td className="px-5 py-3 text-text">{i.email}</td>
                    <td className="py-3 pr-4">
                      <Badge tone={i.role === 'admin' ? 'gold' : 'neutral'}>{i.role}</Badge>
                    </td>
                    <td className="py-3 pr-4 text-text-muted">Expires {formatDateTime(i.expires_at)}</td>
                    <td className="py-3 pr-5 text-right">
                      <button
                        onClick={() => revoke.mutate(i.id)}
                        disabled={revoke.isPending}
                        className="text-sm text-danger hover:underline disabled:opacity-60"
                      >
                        Revoke
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      <section className="rounded-lg border border-border bg-surface p-5">
        <h2 className="font-medium text-text">Join another organization</h2>
        <p className="mt-1 text-sm text-text-muted">
          Were you sent an invitation link or token? Paste it here to join that organization with this account.
          (The invitation has to be addressed to {user?.email}.)
        </p>
        <div className="mt-3">
          <JoinOrganizationForm />
        </div>
      </section>
    </div>
  )
}
