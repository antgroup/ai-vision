import { z } from "zod"

// Space type enum
export const SpaceType = {
  Personal: 1,
  Business: 2,
} as const

// Space scope enum
export const SpaceScope = {
  Private: 1,
  Public: 2,
} as const

// Space status enum
export const SpaceStatus = {
  Disabled: 0,
  Normal: 1,
} as const

// Single Space
export const spaceSchema = z
  .object({
    space_id: z.string(),
    name: z.string(),
    type: z.union([z.literal(1), z.literal(2)]),
    scope: z.union([z.literal(1), z.literal(2)]),
    description: z.string().nullable(),
    creator_name: z.string(),
    owner_uuid: z.string().nullable(),
    is_owner: z.boolean(),
    can_manage_admins: z.boolean(),
    can_transfer_ownership: z.boolean(),
    member_count: z.number(),
    agent_count: z.number(),
    status: z.union([z.literal(0), z.literal(1)]),
    gmt_create: z.string(),
    role: z.number(),
    permissions: z.array(z.string()),
    collaboration_enabled: z.boolean(),
  })
  .transform(({ space_id, ...space }) => ({ id: space_id, ...space }))

export type Space = z.infer<typeof spaceSchema>

// Space list response (no pagination — returns all user spaces)
export const spaceListResponseSchema = z.object({
  code: z.number(),
  message: z.string(),
  data: z.object({
    items: z.array(spaceSchema),
  }),
})

export type SpaceListResponse = z.infer<typeof spaceListResponseSchema>

// Space detail (extends list item with creator info and role)
export const spaceDetailSchema = spaceSchema

export const spaceDetailResponseSchema = z.object({
  code: z.number(),
  message: z.string(),
  data: spaceDetailSchema,
})

export type SpaceDetail = z.infer<typeof spaceDetailSchema>

// Standard API response wrapper (for error handling)
export const apiResponseSchema = z.object({
  code: z.number(),
  message: z.string(),
})
