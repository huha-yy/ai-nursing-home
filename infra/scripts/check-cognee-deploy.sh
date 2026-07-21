#!/bin/bash
# cognee Phase 2 部署检查脚本
# 用法: bash infra/scripts/check-cognee-deploy.sh

set -e
cd "$(dirname "$0")/../.."

echo "=== (1)(2) 模型下载状态 ==="
CID=$(docker ps --filter name=dl-cognee-model-download --format '{{.Names}}' | head -1)
if [ -z "$CID" ]; then
  echo "容器不存在（compose 未包含该服务）。检查 volume 内容..."
  docker run --rm -v dato_cognee_hf_models:/data alpine sh -c "du -sh /data/hub/models--* 2>&1"
else
  echo "容器状态: $(docker ps --filter name=dl-cognee-model-download --format '{{.Status}}')"
  echo "退出码: $(docker wait dl-cognee-model-download 2>&1)"
fi

echo ""
echo "=== (3) 容器运行状态 ==="
COGNEE=$(docker ps --filter "name=dl-cognee$" --format '{{.Names}}')
RERANKER=$(docker ps --filter name=dl-cognee-reranker --format '{{.Names}}')
echo "dl-cognee:         $COGNEE ($(docker ps --filter "name=dl-cognee$" --format '{{.Status}}'))"
echo "dl-cognee-reranker: $RERANKER ($(docker ps --filter name=dl-cognee-reranker --format '{{.Status}}'))"

echo ""
echo "=== (4) health + reembed ==="
docker exec "$COGNEE" .venv/bin/python -c "import httpx; print('health:', httpx.get('http://localhost:8080/health').json())"
docker exec "$COGNEE" .venv/bin/python -m dl_cognee.scripts.reembed 2>&1

echo ""
echo "=== (5) 验证搜索端 ==="
echo "宿主机 curl :8080: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/v1/search -X POST -H 'Content-Type: application/json' -d '{"query":"test","limit":1}') (401=正常，需agent token)"
echo "容器内 search: $(docker exec "$COGNEE" .venv/bin/python -c "import httpx; print(httpx.post('http://localhost:8080/v1/search', json={'query':'test','limit':1}).status_code)" 2>/dev/null)"
echo "容器内 reranker: $(docker exec "$RERANKER" .venv/bin/python -c "
import httpx
r = httpx.post('http://localhost:8080/rerank', json={'query':'bge','candidates':['bge-m3 1024-dim','fastembed old','pgvector stores'],'top_k':2})
if r.status_code==200:
    s=[f\"{x['score']:.2f}\" for x in r.json()['results']]
    print(f'ok {s} (bge-m3 first=\"{\"yes\" if float(s[0])>float(s[1]) else \"no\"}\")')
else: print(f'error {r.status_code}')
" 2>/dev/null)"

echo ""
echo "=== 全部完成 ==="
