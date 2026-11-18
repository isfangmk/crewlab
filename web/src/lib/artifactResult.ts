// run_python 工具结果里是否带产出物。
// 与 MessageBubble.interpreterArtifactsOf 同一口径：result.artifacts 是非空数组才算有产物。
// 侧栏据此决定是否自动展开——只 print 不产文件时 artifacts 为空，不应弹面板。

export function toolResultHasArtifacts(result: unknown): boolean {
  if (typeof result !== 'object' || result === null) return false;
  const artifacts = (result as { artifacts?: unknown }).artifacts;
  return Array.isArray(artifacts) && artifacts.length > 0;
}
