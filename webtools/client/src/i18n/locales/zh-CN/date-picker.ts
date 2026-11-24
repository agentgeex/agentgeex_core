/**
 * Date / month / year / time pickers (shared by ui/date-picker and ui/calendar)
 * ★ These strings come from shadcn component defaults — a default written in a function parameter is evaluated
 *   **at call time**, where the hook is unavailable, so they are all moved to `?? t(...)` inside the function body.
 */
export default {
  clear: "清除",

  start: "开始",
  end: "结束",
  startDate: "开始日期",
  endDate: "结束日期",
  startMonth: "开始月份",
  endMonth: "结束月份",
  startYear: "开始年份",
  endYear: "结束年份",
  startDateTime: "开始日期时间",
  endDateTime: "结束日期时间",

  chooseDate: "选择日期",
  chooseDateRange: "选择日期范围",
  chooseMonth: "选择月份",
  chooseMonthRange: "选择月份范围",
  chooseYear: "选择年份",
  chooseYearRange: "选择年份范围",
  chooseDateTime: "选择日期时间",
  chooseDateTimeRange: "选择日期时间范围",

  prevYear: "上一年",
  nextYear: "下一年",
  prevStep: "上一步",
  nextStep: "下一步",

  time: "时间",
  startTime: "开始时间",
  endTime: "结束时间",
  today: "今天",

  week: {
    sun: "日",
    mon: "一",
    tue: "二",
    wed: "三",
    thu: "四",
    fri: "五",
    sat: "六",
  },

  month: {
    m1: "1月",
    m2: "2月",
    m3: "3月",
    m4: "4月",
    m5: "5月",
    m6: "6月",
    m7: "7月",
    m8: "8月",
    m9: "9月",
    m10: "10月",
    m11: "11月",
    m12: "12月",
  },

  title: { yearMonth: "{{y}} 年 {{mLabel}} 月", year: "{{y}} 年" },

  nav: {
    prevDecade: "上十年",
    prevYear: "上一年",
    prevMonth: "上个月",
    nextMonth: "下个月",
    nextYear: "下一年",
    nextDecade: "下十年",
  },
} as const;
