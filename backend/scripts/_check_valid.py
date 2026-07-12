import json

d = json.load(open(r'D:\python\PythonProject\RAG+LLMProject\backend\scripts\eval_output\generation_results.json','r',encoding='utf-8'))
j = json.load(open(r'D:\python\PythonProject\RAG+LLMProject\backend\scripts\eval_output\judge_results.json','r',encoding='utf-8'))

models = ['qwen-turbo','qwen-plus','qwen-max','deepseek-v3','glm-4-plus']
valid = {m: {'acc':[], 'faith':[], 'cit':[], 'comp':[]} for m in models}

for qid, item in d.items():
    if item['context_count'] == 0:
        continue
    for m in models:
        js = j.get(qid, {}).get(m)
        if js:
            valid[m]['acc'].append(js['accuracy'])
            valid[m]['faith'].append(js['faithfulness'])
            valid[m]['cit'].append(js['citation_accuracy'])
            valid[m]['comp'].append(js['completeness'])

count = sum(1 for v in d.values() if v['context_count'] > 0)
print(f"\n=== 仅统计有检索结果的题目 ({count} 条) ===\n")
print(f"{'模型':<15} {'准确性':>8} {'无幻觉':>8} {'引用准确':>8} {'完整度':>8} {'综合':>8}")
print('-' * 55)
for m in models:
    v = valid[m]
    if v['acc']:
        avg = lambda l: round(sum(l)/len(l), 1)
        o = avg([avg(v['acc']), avg(v['faith']), avg(v['cit']), avg(v['comp'])])
        print(f"{m:<15} {avg(v['acc']):>7.1f}% {avg(v['faith']):>7.1f}% {avg(v['cit']):>7.1f}% {avg(v['comp']):>7.1f}% {o:>7.1f}%")
