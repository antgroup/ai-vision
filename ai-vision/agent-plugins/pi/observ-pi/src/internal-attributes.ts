/** Repository-owned attributes that callers must not override through config. */
export const InternalAttributes = {
  META_ROOT: '_internal.meta.root',
} as const;

export const INTERNAL_META_ROOT_VALUE = '1';
