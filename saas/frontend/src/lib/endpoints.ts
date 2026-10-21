import { ApiError, apiFetch } from './api'
import type {
  AssetDetailOut,
  AssetOut,
  BronzeDeployIn,
  BronzePipelineIn,
  ReferenceTableIn,
  SilverDeployIn,
  SilverPipelineIn,
  ConnectionIn,
  ConnectionOut,
  DiscoverIn,
  DiscoverOut,
  DriftOut,
  InviteCreatedOut,
  InviteOut,
  MemberOut,
  MeOut,
  OrgRunOut,
  Role,
  PipelineDeployIn,
  PipelineDetailOut,
  PipelineIn,
  PipelineOut,
  PipelinePreviewOut,
  MembershipOut,
  RegisterOut,
  RunOut,
  SourceOut,
  TokenOut,
  UsageOut,
} from './types'

export const auth = {
  register: (body: { email: string; password: string; org_name: string }) =>
    apiFetch<RegisterOut>('/auth/register', { method: 'POST', body }),

  login: (email: string, password: string) =>
    apiFetch<TokenOut>('/auth/login', { method: 'POST', form: { username: email, password } }),

  /** For someone WITHOUT an account yet: the invitation fixes the email, this sets the password. */
  acceptInviteNew: (token: string, password: string) =>
    apiFetch<RegisterOut>('/auth/accept-invite', { method: 'POST', body: { token, password } }),
}

export const account = {
  me: (token: string) => apiFetch<MeOut>('/me', { token }),

  usage: (token: string, orgId: string) => apiFetch<UsageOut>('/org/usage', { token, orgId }),
}

export const connection = {
  /** null means "not connected yet" (the API answers 404) rather than an error to surface. */
  get: async (token: string, orgId: string): Promise<ConnectionOut | null> => {
    try {
      return await apiFetch<ConnectionOut>('/org/connection', { token, orgId })
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null
      throw err
    }
  },

  connect: (token: string, orgId: string, body: ConnectionIn) =>
    apiFetch<ConnectionOut>('/org/connection', { method: 'PUT', token, orgId, body }),

  disconnect: (token: string, orgId: string) =>
    apiFetch<void>('/org/connection', { method: 'DELETE', token, orgId }),
}

export const sources = {
  list: (token: string, orgId: string, sourceType?: string) =>
    apiFetch<SourceOut[]>(`/sources${sourceType ? `?source_type=${sourceType}` : ''}`, { token, orgId }),

  history: (token: string, orgId: string, sourceId: string) =>
    apiFetch<SourceOut[]>(`/sources/${sourceId}/history`, { token, orgId }),

  drift: (token: string, orgId: string) => apiFetch<DriftOut[]>('/drift', { token, orgId }),
}

export const discovery = {
  discover: (token: string, orgId: string, body: DiscoverIn) =>
    apiFetch<DiscoverOut>('/discover', { method: 'POST', token, orgId, body }),
}

export const pipelines = {
  list: (token: string, orgId: string) => apiFetch<PipelineOut[]>('/pipelines', { token, orgId }),

  get: (token: string, orgId: string, id: string) =>
    apiFetch<PipelineDetailOut>(`/pipelines/${id}`, { token, orgId }),

  preview: (token: string, orgId: string, body: PipelineIn) =>
    apiFetch<PipelinePreviewOut>('/pipelines/preview', { method: 'POST', token, orgId, body }),

  deploy: (token: string, orgId: string, body: PipelineDeployIn) =>
    apiFetch<PipelineDetailOut>('/pipelines', { method: 'POST', token, orgId, body }),

  remove: (token: string, orgId: string, id: string) =>
    apiFetch<void>(`/pipelines/${id}`, { method: 'DELETE', token, orgId }),
}

export const runs = {
  list: (token: string, orgId: string, pipelineId: string) =>
    apiFetch<RunOut[]>(`/pipelines/${pipelineId}/runs`, { token, orgId }),

  runNow: (token: string, orgId: string, pipelineId: string) =>
    apiFetch<RunOut>(`/pipelines/${pipelineId}/run`, { method: 'POST', token, orgId }),

  refresh: (token: string, orgId: string, pipelineId: string) =>
    apiFetch<RunOut[]>(`/pipelines/${pipelineId}/runs/refresh`, { method: 'POST', token, orgId }),
}

export const team = {
  members: (token: string, orgId: string) => apiFetch<MemberOut[]>('/org/members', { token, orgId }),

  invites: (token: string, orgId: string) => apiFetch<InviteOut[]>('/org/invites', { token, orgId }),

  invite: (token: string, orgId: string, body: { email: string; role: Role }) =>
    apiFetch<InviteCreatedOut>('/org/invites', { method: 'POST', token, orgId, body }),

  revokeInvite: (token: string, orgId: string, inviteId: string) =>
    apiFetch<void>(`/org/invites/${inviteId}`, { method: 'DELETE', token, orgId }),

  removeMember: (token: string, orgId: string, userId: string) =>
    apiFetch<void>(`/org/members/${userId}`, { method: 'DELETE', token, orgId }),

  /** For someone who already has an account: join the org an invitation was addressed to. */
  acceptInvite: (token: string, inviteToken: string) =>
    apiFetch<MembershipOut>('/invites/accept', { method: 'POST', token, body: { token: inviteToken } }),
}

export const orgRuns = {
  list: (token: string, orgId: string, status?: string) =>
    apiFetch<OrgRunOut[]>(`/runs${status ? `?status=${status}` : ''}`, { token, orgId }),
}

export const assets = {
  list: (token: string, orgId: string, layer?: string) =>
    apiFetch<AssetOut[]>(`/assets${layer ? `?layer=${layer}` : ''}`, { token, orgId }),

  get: (token: string, orgId: string, id: string) =>
    apiFetch<AssetDetailOut>(`/assets/${id}`, { token, orgId }),

  registerReference: (token: string, orgId: string, body: ReferenceTableIn) =>
    apiFetch<AssetDetailOut>('/assets/reference', { method: 'POST', token, orgId, body }),
}

export const silver = {
  preview: (token: string, orgId: string, body: SilverPipelineIn) =>
    apiFetch<PipelinePreviewOut>('/silver/preview', { method: 'POST', token, orgId, body }),

  deploy: (token: string, orgId: string, body: SilverDeployIn) =>
    apiFetch<PipelineDetailOut>('/silver', { method: 'POST', token, orgId, body }),
}

export const bronze = {
  preview: (token: string, orgId: string, body: BronzePipelineIn) =>
    apiFetch<PipelinePreviewOut>('/bronze/preview', { method: 'POST', token, orgId, body }),

  deploy: (token: string, orgId: string, body: BronzeDeployIn) =>
    apiFetch<PipelineDetailOut>('/bronze', { method: 'POST', token, orgId, body }),
}
