import { describe, expect, it } from 'vitest';

import { toolResultHasArtifacts } from './artifactResult';

describe('toolResultHasArtifacts', () => {
  it('无 result / 非对象时不算有产物', () => {
    expect(toolResultHasArtifacts(undefined)).toBe(false);
    expect(toolResultHasArtifacts(null)).toBe(false);
    expect(toolResultHasArtifacts('ok')).toBe(false);
  });

  it('artifacts 缺失或为空数组时不算有产物', () => {
    expect(toolResultHasArtifacts({ status: 'ok' })).toBe(false);
    expect(toolResultHasArtifacts({ artifacts: [] })).toBe(false);
    expect(toolResultHasArtifacts({ artifacts: 'chart.png' })).toBe(false);
  });

  it('artifacts 非空时算有产物', () => {
    expect(toolResultHasArtifacts({ artifacts: [{ id: 'a1' }] })).toBe(true);
    expect(toolResultHasArtifacts({ artifacts: [{}] })).toBe(true);
  });
});
