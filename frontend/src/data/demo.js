// Entirely fictional examples. This module has no API or storage dependencies.
const bi = (zh, en) => ({ zh, en });
export const records = [
  { id: "P-001", domain: "policy", title: bi("澄江市软件采购指引（演示）", "Chengjiang software procurement guide (sample)"),
    summary: bi("软件项目供应商须提供主体证明、同类项目案例与服务方案。", "Software suppliers must provide registration, a comparable project and a service plan."),
    text: bi("第 3 条：软件采购供应商须提交有效主体登记证明、至少 1 项同类软件项目案例以及含响应时间和维护范围的服务方案。第 4 条：评审由技术方案、服务方案及报价三部分组成。本指引仅用于虚构演示，不是实际法规。", "Clause 3: Software suppliers must submit valid registration, at least one comparable software project and a service plan covering response time and maintenance scope. Clause 4: Evaluation considers technical proposal, service plan and price. This fictional guide is for demonstration and is not actual regulation."),
    fields: [ [bi("适用范围", "Scope"), bi("软件采购", "Software procurement")], [bi("发布日", "Published"), bi("2026-06-01", "2026-06-01")] ] },
  { id: "T-001", domain: "tender", title: bi("澄江市知识服务平台", "Chengjiang knowledge services platform"),
    summary: bi("预算 120 万元；已中标；中标企业为云川科技。", "CNY 1.2M budget; awarded to Yunchuan Technology."),
    text: bi("项目 T-001：澄江市知识服务平台。采购内容为知识检索、问答界面和业务系统集成；预算 120 万元，中标金额 108 万元；中标企业为云川科技；公告日期为 2026-06-18。以上均为虚构演示数据。", "Project T-001: Chengjiang knowledge services platform. Scope: knowledge search, Q&A interface and business integration. Budget: CNY 1.2M; awarded amount: CNY 1.08M; winner: Yunchuan Technology; announced 2026-06-18. All details are fictional."),
    fields: [ [bi("预算", "Budget"), bi("120 万元", "CNY 1.2M")], [bi("中标企业", "Winner"), bi("云川科技", "Yunchuan Technology")], [bi("公告日期", "Announced"), bi("2026-06-18", "2026-06-18")] ] },
  { id: "T-002", domain: "tender", title: bi("澄江市档案管理软件", "Chengjiang archive management software"),
    summary: bi("预算 80 万元；正在征集；包含检索和权限管理。", "CNY 800K budget; open procurement; search and access management."),
    text: bi("项目 T-002：澄江市档案管理软件。预算 80 万元；状态为正在征集；采购内容为档案检索和权限管理；公告日期为 2026-07-02。未产生中标结果。以上均为虚构演示数据。", "Project T-002: Chengjiang archive management software. Budget: CNY 800K; open procurement; scope: archive search and access management; announced 2026-07-02. No award has been made. All details are fictional."),
    fields: [ [bi("预算", "Budget"), bi("80 万元", "CNY 800K")], [bi("状态", "Status"), bi("正在征集", "Open procurement")], [bi("公告日期", "Announced"), bi("2026-07-02", "2026-07-02")] ] },
  { id: "E-001", domain: "enterprise", title: bi("云川科技 · 企业档案", "Yunchuan Technology · Company profile"),
    summary: bi("澄江市软件服务企业，业务包括知识检索与系统集成。", "A Chengjiang software company working on knowledge search and systems integration."),
    text: bi("企业 E-001：云川科技。所在地区为澄江市；经营范围为软件开发、知识检索和系统集成；档案记录中有知识服务平台同类案例。当前样例没有主体登记证明或服务方案附件，不能据此确认供应商资格。此企业为虚构。", "Company E-001: Yunchuan Technology, based in Chengjiang. Scope: software development, knowledge search and systems integration. Its sample profile records a comparable knowledge services project. Registration evidence and service plan attachments are absent, so supplier eligibility cannot be confirmed. This company is fictional."),
    fields: [ [bi("地区", "Region"), bi("澄江市", "Chengjiang")], [bi("业务", "Business"), bi("知识检索 / 系统集成", "Knowledge search / Integration")] ] },
];

export const cases = [
  { id: "policy", domain: "policy", label: bi("读懂业务规则", "Understand requirements"), question: bi("软件采购供应商需要准备什么？", "What must a software supplier prepare?"),
    aliases: ["供应商资格", "policy", "supplier requirements"], sources: ["P-001"],
    answer: bi("根据样例采购指引，供应商需要准备三类材料：\n\n1. 有效主体登记证明。\n2. 至少 1 项同类软件项目案例。\n3. 写明响应时间与维护范围的服务方案。\n\n依据：P-001 第 3 条。该指引为虚构样例，不构成实际法规或资格认定。", "The sample guide requires three items:\n\n1. Valid company registration.\n2. At least one comparable software project.\n3. A service plan covering response time and maintenance scope.\n\nEvidence: P-001, clause 3. This fictional guide is not actual regulation or an eligibility determination."),
    followup: { question: bi("评审会考虑哪些方面？", "What does the evaluation consider?"), sources: ["P-001"], answer: bi("样例指引第 4 条列出技术方案、服务方案和报价三个方面。样例没有提供权重或评分细则，因此不能推断各部分分值。", "Clause 4 lists technical proposal, service plan and price. The sample does not provide weights or scoring rules, so individual scores cannot be inferred.") } },
  { id: "tender", domain: "tender", label: bi("筛选业务数据", "Filter business records"), question: bi("有哪些预算超过 100 万的软件项目？", "Which software projects have budgets above CNY 1M?"),
    aliases: ["预算超过100万", "项目筛选", "tender", "budget above 1m"], sources: ["T-001", "T-002"],
    answer: bi("当前样例中有 1 个符合条件的项目：\n\n澄江市知识服务平台（T-001）\n预算：120 万元 · 状态：已中标\n中标企业：云川科技\n\n档案管理软件（T-002）预算为 80 万元，未达到“超过 100 万元”的筛选条件。结果仅覆盖这两条虚构样例记录。", "One project in the sample meets the condition:\n\nChengjiang knowledge services platform (T-001)\nBudget: CNY 1.2M · Status: awarded\nWinner: Yunchuan Technology\n\nArchive management software (T-002) has a CNY 800K budget and does not exceed CNY 1M. This result covers only these two fictional records."),
    followup: { question: bi("这个项目的中标金额是多少？", "What was the awarded amount for that project?"), sources: ["T-001"], answer: bi("澄江市知识服务平台（T-001）的样例中标金额为 108 万元，预算为 120 万元。中标企业为云川科技。", "The sample awarded amount for the knowledge services platform (T-001) is CNY 1.08M, against a CNY 1.2M budget. The winner is Yunchuan Technology.") } },
  { id: "enterprise", domain: "enterprise", label: bi("了解合作企业", "Explore a company"), question: bi("云川科技有哪些业务？", "What does Yunchuan Technology do?"),
    aliases: ["云川科技", "企业查询", "enterprise", "yunchuan technology"], sources: ["E-001"],
    answer: bi("云川科技是虚构的澄江市软件服务企业，样例档案列出的业务包括：\n\n• 软件开发\n• 知识检索\n• 系统集成\n\n档案有同类知识服务项目记录，但未提供登记证明或服务方案附件；这些信息不能单独证明其供应商资格。", "Yunchuan Technology is a fictional software services company in Chengjiang. Its sample profile lists:\n\n• Software development\n• Knowledge search\n• Systems integration\n\nA comparable knowledge project is recorded, but registration evidence and service plan attachments are absent. The profile alone does not establish supplier eligibility."),
    followup: { question: bi("它中标了哪个样例项目？", "Which sample project did it win?"), sources: ["E-001", "T-001"], answer: bi("按样例企业名称关联项目公告，云川科技中标了澄江市知识服务平台（T-001），中标金额 108 万元。当前样例仅包含这一条已中标记录。", "Matching the sample company name to the tender announcement shows that Yunchuan Technology won the Chengjiang knowledge services platform (T-001) for CNY 1.08M. This is the only awarded record in the sample.") } },
  { id: "association", domain: "enterprise", label: bi("连接多份证据", "Connect the evidence"), question: bi("云川科技是否符合样例采购要求？", "Does Yunchuan meet the sample procurement requirements?"),
    aliases: ["跨领域", "资格判断", "association", "check eligibility"], sources: ["P-001", "E-001", "T-001"],
    answer: bi("证据不足，无法确认完全符合。\n\n要求：样例指引需要主体登记证明、同类项目案例及服务方案（P-001）。\n已有证据：企业档案与中标公告支持同类知识服务项目案例（E-001、T-001）。\n缺失证据：当前样例没有主体登记证明和服务方案附件。\n\n因此，可以确认样例中存在同类项目记录，但不能直接给出“具备资格”的结论。", "There is insufficient evidence to confirm full eligibility.\n\nRequirement: the guide calls for registration, a comparable project and a service plan (P-001).\nAvailable: the company profile and award announcement support a comparable knowledge project (E-001, T-001).\nMissing: registration evidence and service plan attachments.\n\nA comparable project is documented in the sample; a complete eligibility conclusion is not supported."),
    followup: { question: bi("还需要补充什么证据？", "What additional evidence is needed?"), sources: ["P-001", "E-001"], answer: bi("根据样例要求，需要补充有效主体登记证明，以及包含响应时间和维护范围的服务方案。还需核验同类项目案例的实际证明材料；样例记录不能替代正式文件。", "The sample requirements call for valid registration evidence and a service plan covering response time and maintenance scope. Actual supporting documents for the comparable project also need checking; sample records do not replace formal documents.") } },
];

export const normalizeQuestion = (value) => value.toLowerCase().replace(/[\s?？。，,.!！]/g, "").trim();
export function matchQuestion(question) {
  const input = normalizeQuestion(question);
  if (!input) return null;
  for (const item of cases) {
    if ([...Object.values(item.question), ...item.aliases].some((q) => normalizeQuestion(q) === input)) return { ...item, followupAnswer: false };
    if (Object.values(item.followup.question).some((q) => normalizeQuestion(q) === input)) return { ...item.followup, id: item.id, domain: item.domain, followupAnswer: true };
  }
  return null;
}
export function searchRecords(query, domain = "all") {
  const input = query.trim().toLowerCase();
  return records.filter((record) => (domain === "all" || record.domain === domain) &&
    JSON.stringify([record.id, record.title, record.summary, record.fields]).toLowerCase().includes(input));
}
export function localizeRecord(record, language) {
  return { domain: record.domain, record_id: record.id, title: record.title[language], summary: record.summary[language],
    excerpt: record.text[language], key_fields: Object.fromEntries(record.fields.map(([k, v]) => [k[language], v[language]])), attachments: [] };
}
