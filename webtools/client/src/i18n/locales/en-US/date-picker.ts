/**
 * Date / month / year / time pickers (shared by ui/date-picker and ui/calendar)
 * These strings come from component defaults — defaults evaluate at call time without hooks,
 * so every one is resolved in the body via `?? t(...)`.
 */
export default {
  clear: "Clear",

  start: "Start",
  end: "End",
  startDate: "Start date",
  endDate: "End date",
  startMonth: "Start month",
  endMonth: "End month",
  startYear: "Start year",
  endYear: "End year",
  startDateTime: "Start date & time",
  endDateTime: "End date & time",

  chooseDate: "Pick a date",
  chooseDateRange: "Pick a date range",
  chooseMonth: "Pick a month",
  chooseMonthRange: "Pick a month range",
  chooseYear: "Pick a year",
  chooseYearRange: "Pick a year range",
  chooseDateTime: "Pick a date & time",
  chooseDateTimeRange: "Pick a date & time range",

  prevYear: "Previous year",
  nextYear: "Next year",
  prevStep: "Previous",
  nextStep: "Next",

  time: "Time",
  startTime: "Start time",
  endTime: "End time",
  today: "Today",

  week: { sun: "Su", mon: "Mo", tue: "Tu", wed: "We", thu: "Th", fri: "Fr", sat: "Sa" },

  month: {
    m1: "Jan",
    m2: "Feb",
    m3: "Mar",
    m4: "Apr",
    m5: "May",
    m6: "Jun",
    m7: "Jul",
    m8: "Aug",
    m9: "Sep",
    m10: "Oct",
    m11: "Nov",
    m12: "Dec",
  },

  title: { yearMonth: "{{mLabel}} {{y}}", year: "{{y}}" },

  nav: {
    prevDecade: "Previous decade",
    prevYear: "Previous year",
    prevMonth: "Previous month",
    nextMonth: "Next month",
    nextYear: "Next year",
    nextDecade: "Next decade",
  },
} as const;
