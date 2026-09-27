import { readApiError } from "./referenceMediaApi";
import { getPreproductionWorkspace } from "./preproductionApi";

export type ProductImage = {id:string;title:string;pageUrl:string;imageUrl:string;previewUrl:string};
export type ProductImageSearch = {searchId:string;productName:string;candidates:ProductImage[]};
const base=(projectId:string)=>`/api/projects/${encodeURIComponent(projectId)}/preproduction/product-images`;
async function post<T>(url:string,body:unknown):Promise<T>{
  let response:Response;
  try {response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json","x-aivre-intent":"semantic-analysis"},body:JSON.stringify(body)});}
  catch {throw new Error("无法连接本地服务，请重试。");}
  if(!response.ok) throw new Error(await readApiError(response,"商品图片操作失败，请重试或上传图片。"));
  return response.json() as Promise<T>;
}
export const searchProductImages=(projectId:string,productName:string)=>post<ProductImageSearch>(`${base(projectId)}/search`,{productName});
export async function importProductImages(projectId:string,searchId:string,candidateIds:string[]){
  const imported=await post<{importedAssetIds:string[]}>(`${base(projectId)}/import`,{searchId,candidateIds,confirmed:true});
  const workspace=await getPreproductionWorkspace(projectId);
  return {...imported,workspace};
}
