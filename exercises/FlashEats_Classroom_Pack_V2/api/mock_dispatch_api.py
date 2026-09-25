from flask import Flask, request, jsonify
from pathlib import Path
import json
app=Flask(__name__)
DATA=json.loads((Path(__file__).parent/'dispatch_data.json').read_text())
page_hits={}
@app.get('/health')
def health(): return jsonify({'status':'ok','service':'flasheats-dispatch-api'})
@app.get('/dispatch/orders')
def list_orders():
    page=max(1,int(request.args.get('page',1))); page_size=min(200,max(1,int(request.args.get('page_size',50))))
    page_hits[page]=page_hits.get(page,0)+1
    if page==3 and page_hits[page]==1: return jsonify({'error':'temporary upstream failure','retryable':True}),500
    if page==5 and page_hits[page]==1: return jsonify({'error':'rate limit exceeded','retry_after_seconds':1}),429
    start=(page-1)*page_size; end=start+page_size; items=DATA[start:end]
    return jsonify({'data':items,'page':page,'page_size':page_size,'has_more':end<len(DATA),'total_records':len(DATA)})
@app.get('/dispatch/orders/<order_id>')
def get_order(order_id):
    x=next((x for x in DATA if x['order_id']==order_id),None)
    return (jsonify(x),200) if x else (jsonify({'error':'order not found','order_id':order_id}),404)
if __name__=='__main__': app.run(host='0.0.0.0',port=8000,debug=False)
