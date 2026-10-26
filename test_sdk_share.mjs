import assert from 'node:assert/strict';
import {validatedWork} from './sdk_share.mjs';
assert.equal(validatedWork({itemId:'7698772565188654321',title:'真实作品'}).itemId,'7698772565188654321');
for(const work of [{itemId:'bad'},{itemId:'7698772565188654321',to:'other'},{itemId:'7698772565188654321',coverUrl:'https://example.com/a.jpg'},{itemId:'7698772565188654321',imageCount:0}])assert.throws(()=>validatedWork(work));
console.log('5 native work-card validation checks passed');
