// Mirrors saas/backend/app/schemas.py — keep in sync by hand for now.

export interface OrgOut {
  id: string
  name: string
  tier: string
}

export interface MembershipOut {
  org: OrgOut
  role: 'admin' | 'member'
}

export interface MeOut {
  user_id: string
  email: string
  memberships: MembershipOut[]
}

export interface TokenOut {
  access_token: string
  token_type: 'bearer'
}

export interface RegisterOut {
  user_id: string
  email: string
  org: OrgOut
  access_token: string
  token_type: 'bearer'
}

export interface UsageOut {
  org_id: string
  org_name: string
  tier: string
  pipeline_limit: number
  pipeline_count: number
  can_create_pipeline: boolean
  member_limit: number
  member_count: number
  pending_invites: number
}
