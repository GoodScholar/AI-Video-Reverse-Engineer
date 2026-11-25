import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { CreativeCanvasNodeSearch } from "./CreativeCanvasNodeSearch";
it("名称或类型搜索，方向键和 Enter 命中稳定节点身份", async () => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  const select = vi.fn(); const user = userEvent.setup();
  render(<CreativeCanvasNodeSearch nodes={[{ id:"a", kind:"text", label:"故事",position:{x:0,y:0} },{id:"b",kind:"media",label:"产品",position:{x:1,y:1}}]} onSelect={select} onClose={vi.fn()} />);
  const input=screen.getByRole("textbox",{name:"搜索画布节点"});
  await user.type(input,"素材");
  expect(screen.getAllByRole("option")).toHaveLength(1);
  await user.keyboard("{Enter}"); expect(select).toHaveBeenLastCalledWith("b");
  await user.clear(input); await user.keyboard("{ArrowDown}{Enter}"); expect(select).toHaveBeenLastCalledWith("b");
  await user.type(input,"无匹配"); expect(screen.getByText("没有匹配的节点")).toBeVisible();
});
