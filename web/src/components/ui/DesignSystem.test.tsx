import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Plus, Trash2 } from 'lucide-react';
import { describe, expect, it, vi } from 'vitest';
import { FilterBar } from './FilterBar';
import { SearchInput } from './SearchInput';
import { StatusPill } from './StatusPill';
import { TablePager } from './TableCard';
import { RowAction, RowActionIcon } from './RowAction';
import { SectionCard } from './SectionCard';

describe('设计系统基础组件', () => {
  it('FilterBar 只在传入 footer 时渲染第二行', () => {
    const { container, rerender } = render(<FilterBar>第一行</FilterBar>);
    expect(container.querySelectorAll('section > div')).toHaveLength(1);

    rerender(
      <FilterBar footer={<span>已生效条件</span>}>第一行</FilterBar>,
    );
    expect(screen.getByText('已生效条件')).toBeInTheDocument();
    expect(container.querySelectorAll('section > div')).toHaveLength(2);
  });

  it('SearchInput 支持输入与一键清空', async () => {
    function Example() {
      const [value, setValue] = useState('');
      return <SearchInput value={value} onChange={setValue} placeholder="搜索工具" ariaLabel="搜索工具" />;
    }
    render(<Example />);
    const input = screen.getByPlaceholderText('搜索工具');
    await userEvent.type(input, 'mcp');
    expect(input).toHaveValue('mcp');
    await userEvent.click(screen.getByRole('button', { name: '清空搜索' }));
    expect(input).toHaveValue('');
    expect(screen.queryByRole('button', { name: '清空搜索' })).not.toBeInTheDocument();
  });

  it('StatusPill 用语义色渲染状态', () => {
    render(<StatusPill tone="danger" label="失败" pulse />);
    const pill = screen.getByText('失败').parentElement;
    expect(pill).toHaveClass('text-danger');
    expect(pill?.querySelector('span')).toHaveClass('bg-danger');
  });

  it('TablePager 展示区间并在首页禁用上一页', async () => {
    const onPageChange = vi.fn();
    render(
      <TablePager
        page={0}
        pageSize={20}
        total={45}
        shown={20}
        onPageChange={onPageChange}
      />,
    );
    expect(screen.getByText('1-20 / 共 45 条')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it('TablePager 在末页禁用下一页', () => {
    render(
      <TablePager
        page={2}
        pageSize={20}
        total={45}
        shown={5}
        onPageChange={() => {}}
      />,
    );
    expect(screen.getByText('41-45 / 共 45 条')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
  });

  it('RowAction 固定 28px 高与 11px 字号，危险动作只换语义色', () => {
    render(
      <>
        <RowAction icon={Plus} onClick={() => {}}>运行</RowAction>
        <RowAction icon={Trash2} tone="danger" onClick={() => {}}>删除</RowAction>
      </>,
    );
    const run = screen.getByRole('button', { name: '运行' });
    expect(run).toHaveClass('h-7', 'px-2', 'text-[11px]');
    const remove = screen.getByRole('button', { name: '删除' });
    expect(remove).toHaveClass('h-7', 'text-danger');
  });

  it('RowActionIcon 固定 28x28 并使用 label 作为无障碍名称', () => {
    render(<RowActionIcon icon={Trash2} label="删除" tone="danger" onClick={() => {}} />);
    const button = screen.getByRole('button', { name: '删除' });
    expect(button).toHaveClass('h-7', 'w-7');
    expect(button).toHaveAttribute('title', '删除');
  });

  it('SectionCard 渲染标题、说明与底部操作条', () => {
    render(
      <SectionCard title="模型" description="对话模型唯一来源" footer={<button type="button">保存</button>}>
        内容
      </SectionCard>,
    );
    expect(screen.getByRole('heading', { name: '模型' })).toBeInTheDocument();
    expect(screen.getByText('对话模型唯一来源')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument();
  });
});
