// Validate local work-card data; destination remains the fixed verified owner.
export function validatedWork(input){
  if(!input||typeof input!=='object'||!/^[0-9]{15,25}$/.test(input.itemId))throw Error('Invalid work ID');
  const allowed=['itemId','title','authorUid','authorSecUid','authorName','coverUrl','imageCount'];
  if(Object.keys(input).some(k=>!allowed.includes(k)))throw Error('Unknown work field');
  const work={itemId:input.itemId};
  for(const key of ['title','authorUid','authorSecUid','authorName']){
    if(input[key]!=null){if(typeof input[key]!=='string'||input[key].length>600)throw Error('Invalid card field');work[key]=input[key];}
  }
  if(work.authorUid&&!/^[0-9]+$/.test(work.authorUid))throw Error('Invalid author UID');
  if(input.coverUrl){const u=new URL(input.coverUrl);if(u.protocol!=='https:'||u.username||u.password||!['douyinpic.com','byteimg.com','douyincdn.com'].some(d=>u.hostname===d||u.hostname.endsWith('.'+d)))throw Error('Invalid work cover');work.coverUrl=u.href;}
  if(input.imageCount!=null){if(!Number.isInteger(input.imageCount)||input.imageCount<1||input.imageCount>100)throw Error('Invalid image count');work.imageCount=input.imageCount;}
  return work;
}
