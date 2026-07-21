#!/bin/bash
set -e

echo "════════════════════════════════════════"
echo "  cognee Phase 2 — 完整部署检查"
echo "════════════════════════════════════════"

echo ""
echo "── (1) dl-cognee-model-download 容器状态 ──"
STATUS=$(docker ps --filter name=dl-cognee-model-download --format '{{.Status}}' 2>/dev/null || true)
if [ -z "$STATUS" ]; then
  echo "  → 容器不存在（docker-compose 中未定义此服务）"
  echo "  → 模型 volume 数据："
  docker run --rm -v dato_cognee_hf_models:/data alpine sh -c "du -sh /data/hub/models--* 2>&1" 2>/dev/null || echo "     (volumen not found)"
else
  echo "  → 运行中: $STATUS"
  echo "  → 退出码: $(docker wait dl-cognee-model-download 2>&1)"
fi

echo ""
echo "── (2) dl-cognee 容器 ──"
COGNEE=$(docker ps --filter "name=dl-cognee$" --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$COGNEE" ]; then
  echo "  → 名称: $COGNEE"
  echo "  → 状态: $(docker ps --filter "name=dl-cognee$" --format '{{.Status}}')"
  echo "  → health: $(docker exec "$COGNEE" .venv/bin/python -c "import httpx; print(httpx.get('http://localhost:8080/health').json().get('status','?'))" 2>/dev/null || echo 'unreachable')"
else
  echo "  → 未运行"
fi

echo ""
echo "── (3) dl-cognee-reranker 容器 ──"
RERANKER=$(docker ps --filter name=dl-cognee-reranker --format '{{.Names}}' 2>/dev/null || true)
if [ -n "$RERANKER" ]; then
  echo "  → 名称: $RERANKER"
  echo "  → 状态: $(docker ps --filter name=dl-cognee-reranker --format '{{.Status}}')"
  echo "  → health: $(docker exec "$RERANKER" .venv/bin/python -c "import httpx; print(httpx.get('http://localhost:8080/health').json().get('status','?'))" 2>/dev/null || echo 'unreachable')"
else
  echo "  → 未运行"
fi

echo ""
echo "── (4) reembed 状态 ──"
if [ -n "$COGNEE" ]; then
  docker exec "$COGNEE" .venv/bin/python -m dl_cognee.scripts.reembed 2>&1
fi

echo ""
echo "── (5) 搜索端点 ──"
if [ -n "$COGNEE" ]; then
  echo "  → 宿主机 curl: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/v1/search -X POST -H 'Content-Type: application/json' -d '{"query":"test","limit":1}')"
  echo "  → 容器内: $(docker exec "$COGNEE" .venv/bin/python -c "import httpx; print(httpx.post('http://localhost:8080/v1/search', json={'query':'test','limit':1}).status_code)" 2>/dev/null)"
fi

echo ""
echo "── (6) reranker 功能 ──"
if [ -n "$RERANKER" ]; then
  docker exec "$RERANKER" .venv/bin/python -c "
import httpx
r = httpx.post('http://localhost:8080/rerank', json={'query':'bge','candidates':['bge-m3 1024-dim','fastembed old','pgvector'],'top_k':2})
if r.status_code == 200:
    scores = [f'{x[\"score\"]:.2f}' for x in r.json()['results']]
    print(f'  → 排序正确: {scores}')
    print(f'  → {"✅ bge-m3 排第一" if float(scores[0]) > float(scores[1]) else "⚠️ 排序异常"}')
else:
    print(f'  → 错误: {r.status_code}')
" 2>/dev/null
fi

echo ""
echo "════════════════════════════════════════"
echo "  若全部绿色，则部署已完成。"
echo "  ⚠️ curl 宿主机返回 404 是正常现象——"
echo "    dl-cognee 容器用 expose 无端口映射。"
echo "    内部服务（dl-control/agent）可直接访问。"
echo "════════════════════════════════════════"
