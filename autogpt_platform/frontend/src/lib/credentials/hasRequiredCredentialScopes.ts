export function hasRequiredCredentialScopes(
  grantedScopes: readonly string[] | null | undefined,
  requiredScopes: readonly string[] | null | undefined,
) {
  if (!requiredScopes || requiredScopes.length === 0) {
    return true;
  }

  const normalizedGrantedScopes = new Set(grantedScopes ?? []);
  if (normalizedGrantedScopes.has("*")) {
    return true;
  }

  return requiredScopes.every((scope) => normalizedGrantedScopes.has(scope));
}
