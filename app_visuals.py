"""Fixed local icon metadata; project IDs never become arbitrary asset paths."""
VISUALS={
 'rift':('rift.svg','indigo','虹'),
 'ticket':('ticket.svg','blue','票'),
 'douyin':('douyin.ico','black','抖'),
 'douyin-monitor':('douyin.ico','black','抖'),
 'model-bridge':('model-bridge.svg','green','AI'),
 'multica':('multica.svg','violet','M'),
 'xianyuapis':('xianyu.png','yellow','闲'),
 'xhs-all-in-one':('xhs.ico','red','红'),
 'bilibiliapis':('bilibili.ico','cyan','B'),
 'weiboapis':('weibo.ico','red','微'),
 'jdapis':('jd.ico','red','京'),
 'kuaishou-spider':('kuaishou.svg','orange','快'),
 'kuaishou':('kuaishou.svg','orange','快'),
 'zhihuapis':('zhihu.ico','blue','知'),
 'channel':('channel.svg','indigo','流'),
 'headquarters':('headquarters.svg','green','目'),
 'account-maintenance':('accounts.svg','violet','账'),
 'ops-hub':('ops-hub.svg','blue','运'),
 'coffee-orders':('coffee-orders.svg','coffee','杯'),
}
def app_visual(project_id):
 filename,tone,label=VISUALS.get(project_id,('generic.svg','slate','APP'))
 return {'src':'brands/'+filename,'tone':tone,'label':label}
